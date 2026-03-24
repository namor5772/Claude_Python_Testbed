import sys
IS_WINDOWS = sys.platform == "win32"

import ctypes
if IS_WINDOWS:
    import ctypes.wintypes
    # Fix DPI scaling for desktop automation tools — must run before any window creation.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk
from tkinter import messagebox, filedialog, ttk
from html.parser import HTMLParser
import anthropic
import openai
from google import genai
from google.genai import types as genai_types
from ddgs import DDGS
import httpx
import threading
import queue
import os
import base64
import json
import copy
import csv
import subprocess
import re
import io
import socket
import time
import concurrent.futures
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
        import pygetwindow as gw
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
            "Manage the saved agent instruction library on disk for use by future agent "
            "instances. Does NOT change the currently-running instruction. Actions: list "
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
            "Open or connect to the system browser (Edge on Windows, Edge or Chrome on macOS) and navigate to a URL. "
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

# ── Command safety guardrails ──────────────────────────────────────────────
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

# ── Constants ───────────────────────────────────────────────────────────────

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

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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


# ── Helpers ─────────────────────────────────────────────────────────────────

class HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and return plain text."""

    def __init__(self):
        super().__init__()
        self._text = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in ("p", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
            self._text.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._text.append(data)

    def get_text(self):
        return "".join(self._text).strip()


def extract_text_from_html(html):
    extractor = HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


class _ToolBlock:
    """Thin wrapper so OpenAI/Gemini dict-based tool blocks expose the same
    .name, .id, .input attribute interface as Anthropic's Pydantic objects."""

    def __init__(self, name, id, input):
        self.name = name
        self.id = id
        self.input = input
        self.type = "tool_use"



# ── Main Application ────────────────────────────────────────────────────────

class App:
    def __init__(self, root, launch_instruction=None, headless=False):
        self.root = root
        self._headless = headless
        self.root.title("My Agent")
        self.root.geometry(DEFAULT_GEOMETRY)

        # Check for at least one API key
        self._has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
        self._has_openai = bool(os.environ.get("OPENAI_API_KEY"))
        self._has_gemini = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        if not self._has_anthropic and not self._has_openai and not self._has_gemini:
            messagebox.showerror(
                "API Key Missing",
                "Please set at least one of ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY.",
            )
            self.root.destroy()
            return

        # Claim an instance number via lock files
        self._instance_num = self._claim_instance_number()
        if self._instance_num == 1:
            self._state_file = AGENT_STATE_FILE
        else:
            self._state_file = os.path.join(_BASE_DIR, f"agent_state_{self._instance_num}.json")
        if self._instance_num > 1:
            self.root.title(f"My Agent ({self._instance_num})")

        # Initialize API clients for available providers
        self.client = anthropic.Anthropic() if self._has_anthropic else None
        self.openai_client = openai.OpenAI(
            timeout=httpx.Timeout(600.0, connect=10.0, read=120.0),
        ) if self._has_openai else None
        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.gemini_client = genai.Client(
            api_key=gemini_key,
            http_options={"timeout": 120_000},  # 120s read timeout to prevent hung streams
        ) if self._has_gemini else None
        if self._has_anthropic:
            self.provider = "Anthropic"
        elif self._has_openai:
            self.provider = "OpenAI"
        else:
            self.provider = "Gemini"
        self._openai_model_display_names = {}
        self._gemini_model_display_names = {}
        self._model_display_names = {}
        self.messages = []
        self.queue = queue.Queue()
        self.streaming = False
        self.stop_requested = False
        self.pending_images = []   # list of (base64_data, media_type, filename)
        self._editor_images = []   # working copy while editor is open
        self._screenshot_scale = 1.0
        self._screenshot_offset = (0, 0)  # display origin offset for per-display screenshots
        self._screenshot_dims = (0, 0)    # (width, height) of last screenshot sent to model
        self.debug_enabled = tk.BooleanVar(value=False)
        self.tool_calls_enabled = tk.BooleanVar(value=False)
        self.show_activity = tk.BooleanVar(value=False)
        self.show_thinking = tk.BooleanVar(value=False)
        self.save_thinking = tk.BooleanVar(value=False)
        self.desktop_enabled = tk.BooleanVar(value=False)
        self.browser_enabled = tk.BooleanVar(value=False)
        self.meta_enabled = tk.BooleanVar(value=False)
        self._disabled_confirm_patterns = set()
        self._playwright = None
        self._browser = None
        self._page = None
        self._edge_process = None
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        self.model = DEFAULT_MODEL if self.provider == "Anthropic" else (OPENAI_DEFAULT_MODEL if self.provider == "OpenAI" else GEMINI_DEFAULT_MODEL)
        self.temperature = 1.0
        self.thinking_enabled = False
        self.thinking_effort = "high"
        self.thinking_budget = 8192
        self.thinking_mode = "off"  # off/adaptive/low/medium/high/max (for adaptive models)
        self.text_verbosity = "medium"  # low/medium/high (for gpt-5 family)
        self.instruction_editor_window = None
        self.skills_editor_window = None
        self._skills_refresh_list = None
        self.skills = self._load_skills()
        self.available_models = self._fetch_models_for_provider()

        self._current_response_text = ""
        self._current_thinking_text = ""

        # Agent instruction — the text injected as the first user message
        self.agent_instruction = DEFAULT_INSTRUCTION
        self.agent_instruction_name = ""

        self.setup_ui()
        self._load_last_state()

        try:
            self._save_last_state()
        except Exception:
            pass
        self.root.after(50, self.check_queue)
        self.root.after(5000, self._periodic_save)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if self._headless:
            self.root.withdraw()

        # Auto-launch instruction from -l command-line argument
        self._launch_instruction = launch_instruction
        if self._launch_instruction:
            self.root.after(100, self._auto_launch)

    # ── UI Setup ────────────────────────────────────────────────────────

    def setup_ui(self):
        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_rowconfigure(2, weight=0)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=0)

        # Initialize StringVars for model controls (widgets created in editor)
        self._provider_var = tk.StringVar(value=self.provider)
        self._model_id_list = self.available_models
        self._model_var = tk.StringVar(value=self._get_display_name(self.model))
        self._temp_var = tk.DoubleVar(value=self.temperature)
        self._thinking_var = tk.BooleanVar(value=False)
        self._thinking_strength_var = tk.StringVar(value="high")
        self._thinking_mode_var = tk.StringVar(value="Off")
        self._text_verbosity_var = tk.StringVar(value="Medium")

        # No model widgets until editor is opened
        self._provider_combo = None
        self._model_combo = None
        self._temp_label = None
        self._temp_spin = None
        self._thinking_check = None
        self._thinking_strength_combo = None
        self._thinking_mode_combo = None
        self._verbosity_label = None
        self._verbosity_combo = None

        # Row 0: Chat toolbar — Instruction + model info + Save + START/STOP
        chat_toolbar = tk.Frame(self.root)
        chat_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 0))

        self.instruction_button = tk.Button(
            chat_toolbar, text="Agent Instruction", command=self.open_instruction_editor,
        )
        self.instruction_button.pack(side=tk.LEFT, padx=(0, 8))

        self._update_title()

        tk.Label(chat_toolbar, text="Save Chat as", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self.chat_name_entry = tk.Entry(chat_toolbar, font=("Arial", 10), width=20)
        self.chat_name_entry.pack(side=tk.LEFT, padx=(0, 15))

        self._stop_button = tk.Button(
            chat_toolbar, text="STOP", command=self._stop_agent, width=8,
            font=("Arial", 10, "bold"), state="disabled",
        )
        self._stop_button.pack(side=tk.RIGHT, padx=(5, 0))

        self._start_button = tk.Button(
            chat_toolbar, text="START", command=self._start_agent, width=8,
            font=("Arial", 10, "bold"),
        )
        self._start_button.pack(side=tk.RIGHT, padx=(5, 0))

        # Row 1: Chat display
        self.chat_display = tk.Text(
            self.root, wrap=tk.WORD, state="disabled", font=("Arial", 11)
        )
        self.chat_display.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=10)

        scrollbar = tk.Scrollbar(self.root, command=self.chat_display.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=10, padx=(0, 10))
        self.chat_display.config(yscrollcommand=scrollbar.set)

        # Text tags for styling
        self.chat_display.tag_config(
            "user_label", foreground="#1a5fb4", font=("Arial", 11, "bold")
        )
        self.chat_display.tag_config("user", foreground="#1a5fb4")
        self.chat_display.tag_config(
            "assistant_label", foreground="#2e7d32", font=("Arial", 11, "bold")
        )
        self.chat_display.tag_config("assistant", foreground="#2e7d32")
        self.chat_display.tag_config("error", foreground="#c62828")
        self.chat_display.tag_config(
            "tool_info", foreground="#757575", font=("Arial", 10, "italic")
        )
        self.chat_display.tag_config(
            "image_info", foreground="#6a1b9a", font=("Arial", 10, "italic")
        )
        self.chat_display.tag_config(
            "warning", foreground="#e65100", font=("Arial", 10, "italic")
        )
        self.chat_display.tag_config(
            "debug", foreground="#b06000", font=(MONO_FONT, 9)
        )
        self.chat_display.tag_config(
            "debug_label", foreground="#b06000", font=(MONO_FONT, 9, "bold")
        )
        self.chat_display.tag_config(
            "tool_debug", foreground="#00796b", font=(MONO_FONT, 9)
        )
        self.chat_display.tag_config(
            "tool_debug_label", foreground="#00796b", font=(MONO_FONT, 9, "bold")
        )
        self.chat_display.tag_config(
            "call_counter", foreground="#ffffff", background="#d32f2f",
            font=("Arial", 11, "bold")
        )
        self.chat_display.tag_config(
            "call_counter_subtle", foreground="#ffffff", background="#b06000",
            font=("Arial", 11, "bold")
        )
        self.chat_display.tag_config(
            "thinking", foreground="#b8860b", background="#fffde7",
            font=(MONO_FONT, 9, "italic")
        )
        self.chat_display.tag_config(
            "thinking_label", foreground="#b8860b", background="#fffde7",
            font=(MONO_FONT, 9, "bold italic")
        )

        # Row 2: Checkbox row
        checkbox_frame = tk.Frame(self.root)
        checkbox_frame.grid(row=2, column=0, columnspan=2, pady=(0, 5))

        self.debug_toggle = tk.Checkbutton(
            checkbox_frame, text="Debug", variable=self.debug_enabled,
            font=("Arial", 9),
        )
        self.debug_toggle.pack(side=tk.LEFT, padx=(5, 0))

        self.tool_calls_toggle = tk.Checkbutton(
            checkbox_frame, text="Tool Calls", variable=self.tool_calls_enabled,
            font=("Arial", 9),
        )
        self.tool_calls_toggle.pack(side=tk.LEFT, padx=(5, 0))

        self.activity_toggle = tk.Checkbutton(
            checkbox_frame, text="Activity", variable=self.show_activity,
            font=("Arial", 9),
        )
        self.activity_toggle.pack(side=tk.LEFT, padx=(5, 0))

        self.thinking_toggle = tk.Checkbutton(
            checkbox_frame, text="Show Thinking", variable=self.show_thinking,
            font=("Arial", 9),
        )
        self.thinking_toggle.pack(side=tk.LEFT, padx=(5, 0))

        self.save_thinking_toggle = tk.Checkbutton(
            checkbox_frame, text="Save Thinking", variable=self.save_thinking,
            font=("Arial", 9),
        )
        self.save_thinking_toggle.pack(side=tk.LEFT, padx=(5, 0))


    # ── Model / Thinking Helpers ────────────────────────────────────────

    def _fetch_available_models(self):
        try:
            response = self.client.models.list(limit=100)
            self._model_display_names = {}
            model_ids = []
            for m in response.data:
                self._model_display_names[m.id] = m.display_name
                model_ids.append(m.id)
            return model_ids if model_ids else FALLBACK_MODELS
        except Exception:
            self._model_display_names = {}
            return list(FALLBACK_MODELS)

    def _has_model_widgets(self):
        """Return True if the editor model widgets currently exist."""
        return self._model_combo is not None

    def _on_provider_changed(self, event=None):
        """Handle provider combobox selection change."""
        new_provider = self._provider_var.get()
        if new_provider == self.provider:
            return
        self.provider = new_provider
        # Refresh model list for the new provider
        self.available_models = self._fetch_models_for_provider()
        self._model_id_list = self.available_models
        display_names = [self._get_display_name(mid) for mid in self._model_id_list]
        if self._has_model_widgets():
            self._model_combo["values"] = display_names
        # Select default model for new provider
        if new_provider == "OpenAI":
            default = OPENAI_DEFAULT_MODEL
        elif new_provider == "Gemini":
            default = GEMINI_DEFAULT_MODEL
        else:
            default = DEFAULT_MODEL
        if default in self.available_models:
            self.model = default
        elif self.available_models:
            self.model = self.available_models[0]
        self._model_var.set(self._get_display_name(self.model))
        self._on_model_selected()
        self._update_title()
        self._save_last_state()

    def _forget_all_model_widgets(self):
        """Hide all model parameter widgets to reset pack order."""
        if not self._has_model_widgets():
            return
        for w in (self._temp_label, self._temp_spin,
                  self._thinking_check, self._thinking_strength_combo,
                  self._thinking_mode_label, self._thinking_mode_combo,
                  self._verbosity_label, self._verbosity_combo):
            w.pack_forget()

    def _on_model_selected(self, event=None):
        selected_display = self._model_var.get()
        for mid in self._model_id_list:
            if self._get_display_name(mid) == selected_display:
                self.model = mid
                break
        support = self._model_supports_thinking()
        if self._has_model_widgets():
            # Reset all model param widgets to guarantee correct pack order
            self._forget_all_model_widgets()
            if support == "adaptive" and self.provider == "Anthropic":
                # Adaptive model (Anthropic): show mode combo
                self._thinking_mode_label.config(text="Thinking")
                self._thinking_mode_label.pack(side=tk.LEFT, padx=(10, 5))
                self._thinking_mode_combo.pack(side=tk.LEFT, padx=(0, 10))
                # Populate values: Max only for Opus 4.6
                if self.model == "claude-opus-4-6":
                    self._thinking_mode_combo["values"] = ADAPTIVE_MODE_VALUES
                else:
                    self._thinking_mode_combo["values"] = ADAPTIVE_MODE_VALUES_NO_MAX
                    if self._thinking_mode_var.get() == "Max":
                        self._thinking_mode_var.set("High")
                # Sync state from thinking_mode (may pack temp after combo)
                self._on_thinking_mode_changed()
            elif support == "extended" and self.provider == "OpenAI":
                # GPT-5.1+: show mode combobox with None/Low/.../Xhigh
                self._thinking_mode_label.config(text="Reasoning")
                self._thinking_mode_label.pack(side=tk.LEFT, padx=(10, 5))
                self._thinking_mode_combo.pack(side=tk.LEFT, padx=(0, 10))
                # Build values based on model capabilities
                values = ["None", "Low", "Medium", "High"]
                if self._has_reasoning_xhigh():
                    values.append("Xhigh")
                self._thinking_mode_combo["values"] = values
                # Validate current selection
                current = self._thinking_mode_var.get()
                if current not in values:
                    self._thinking_mode_var.set("None")
                self._on_thinking_mode_changed()
                # Show verbosity after mode combo
                self._verbosity_label.pack(side=tk.LEFT, padx=(10, 5))
                self._verbosity_combo.pack(side=tk.LEFT, padx=(0, 10))
            elif support is not None:
                # Manual thinking model or OpenAI/Gemini reasoning: show checkbox + strength
                self._thinking_check.pack(side=tk.LEFT, padx=(10, 2))
                self._thinking_check.config(state="normal")
                self._update_thinking_strength_options()
                if self.thinking_enabled:
                    self._thinking_strength_combo.pack(side=tk.LEFT, padx=(0, 10))
                    self._thinking_strength_combo.config(state="readonly")
                    if self.provider == "Gemini":
                        self._temp_label.pack(side=tk.LEFT, padx=(10, 5))
                        self._temp_spin.pack(side=tk.LEFT, padx=(0, 10))
                else:
                    if not (self.provider == "OpenAI" and self._is_openai_reasoning_model()):
                        self._temp_label.pack(side=tk.LEFT, padx=(10, 5))
                        self._temp_spin.pack(side=tk.LEFT, padx=(0, 10))
                # Show verbosity for gpt-5.0 family
                if self._has_openai_verbosity():
                    self._verbosity_label.pack(side=tk.LEFT, padx=(10, 5))
                    self._verbosity_combo.pack(side=tk.LEFT, padx=(0, 10))
            else:
                # No thinking support
                self._thinking_var.set(False)
                self.thinking_enabled = False
                self.thinking_mode = "off"
                # gpt-5.x-chat Instant models don't support temperature
                if not self._is_gpt5_chat_model():
                    self._temp_label.pack(side=tk.LEFT, padx=(10, 5))
                    self._temp_spin.pack(side=tk.LEFT, padx=(0, 10))
                # gpt-5.x-chat Instant models support verbosity
                if self._has_openai_verbosity():
                    self._verbosity_label.pack(side=tk.LEFT, padx=(10, 5))
                    self._verbosity_combo.pack(side=tk.LEFT, padx=(0, 10))
        else:
            # No widgets — just update state
            if support is None:
                self._thinking_var.set(False)
                self.thinking_enabled = False
                self.thinking_mode = "off"
        self._update_title()
        self._save_last_state()

    def _on_temp_changed(self):
        try:
            val = self._temp_var.get()
            self.temperature = max(0.0, min(1.0, val))
        except (tk.TclError, ValueError):
            self.temperature = 1.0
        self._temp_var.set(self.temperature)
        self._save_last_state()

    def _model_supports_thinking(self, model_id=None):
        mid = model_id or self.model
        if self.provider == "OpenAI":
            if self._is_openai_reasoning_model(mid):
                if self._has_reasoning_none(mid):
                    return "extended"
                return "adaptive"
            return None
        if self.provider == "Gemini":
            return "adaptive" if self._is_gemini_thinking_model(mid) else None
        if mid in ADAPTIVE_THINKING_MODELS:
            return "adaptive"
        for prefix in MANUAL_THINKING_PREFIXES:
            if mid.startswith(prefix):
                return "manual"
        return None

    def _is_gemini_thinking_model(self, model_id=None):
        mid = model_id or self.model
        if "lite" in mid:
            return False
        return any(mid.startswith(p) for p in GEMINI_THINKING_PREFIXES)

    def _restore_model_params(self, entry, state_file=False):
        """Restore provider, model, temperature, and thinking settings from an instruction entry or state file."""
        has_widgets = self._has_model_widgets()
        # Restore provider first (model list depends on it)
        provider_key = "provider"
        saved_provider = entry.get(provider_key, "Anthropic")
        if saved_provider != self.provider:
            can_switch = (saved_provider == "Anthropic" and self._has_anthropic) or \
                         (saved_provider == "OpenAI" and self._has_openai) or \
                         (saved_provider == "Gemini" and self._has_gemini)
            if can_switch:
                self.provider = saved_provider
                self._provider_var.set(saved_provider)
                self.available_models = self._fetch_models_for_provider()
                self._model_id_list = self.available_models
                display_names = [self._get_display_name(mid) for mid in self._model_id_list]
                if has_widgets:
                    self._model_combo["values"] = display_names
        # Restore model (fall back to first available if saved model doesn't match provider)
        model_key = "last_model" if state_file else "model"
        model = entry.get(model_key, "")
        if model and model in self.available_models:
            self.model = model
            self._model_var.set(self._get_display_name(model))
        elif self.available_models:
            self.model = self.available_models[0]
            self._model_var.set(self._get_display_name(self.model))
        # Restore temperature
        temp = entry.get("temperature")
        if temp is not None:
            self.temperature = max(0.0, min(1.0, float(temp)))
            self._temp_var.set(self.temperature)
        # Restore thinking
        if "thinking_enabled" in entry:
            self.thinking_enabled = entry["thinking_enabled"]
            self.thinking_effort = entry.get("thinking_effort", "high")
            self.thinking_budget = entry.get("thinking_budget", 8192)
            # Restore thinking_mode with backward compat from old entries
            if "thinking_mode" in entry:
                self.thinking_mode = entry["thinking_mode"]
            elif self.thinking_enabled:
                # Old format: map thinking_enabled + thinking_effort → thinking_mode
                self.thinking_mode = self.thinking_effort  # low/medium/high/max
            else:
                self.thinking_mode = "off"
            self._thinking_var.set(self.thinking_enabled)
            # Capitalize thinking_mode for display; handle special values
            if self.thinking_mode == "off":
                self._thinking_mode_var.set("Off")
            elif self.thinking_mode == "none":
                self._thinking_mode_var.set("None")
            else:
                self._thinking_mode_var.set(self.thinking_mode.capitalize())
            support = self._model_supports_thinking()
            if support is None:
                self._thinking_var.set(False)
                self.thinking_enabled = False
                self.thinking_mode = "off"
                self._thinking_mode_var.set("Off")
            elif support == "adaptive" and self.provider == "Anthropic":
                # Adaptive model: _on_model_selected will show/hide correct widgets
                pass
            elif support == "extended":
                # GPT-5.1+: _on_model_selected will show mode combobox
                pass
            else:
                # Manual or OpenAI: restore strength combo
                if support == "adaptive":
                    self._thinking_strength_var.set(self.thinking_effort)
                elif support == "manual":
                    for k, v in BUDGET_PRESETS.items():
                        if v == self.thinking_budget:
                            self._thinking_strength_var.set(k)
                            break
            # Let _on_model_selected handle widget visibility and state
            if has_widgets:
                self._on_model_selected()
            else:
                self._on_thinking_toggled()
        # Restore text verbosity
        self.text_verbosity = entry.get("text_verbosity", "medium")
        self._text_verbosity_var.set(self.text_verbosity.capitalize())
        self._update_title()

    def _on_thinking_toggled(self):
        self.thinking_enabled = self._thinking_var.get()
        if self._has_model_widgets():
            # Reset and re-pack all widgets in correct order via _on_model_selected
            self._on_model_selected()
        self._save_last_state()

    def _update_thinking_strength_options(self):
        if not self._has_model_widgets():
            return
        support = self._model_supports_thinking()
        if support == "adaptive":
            if self.provider == "OpenAI" and self._is_gpt5_family() and self._parse_gpt5_minor() == 0:
                values = ["minimal", "low", "medium", "high"]
            elif self.provider in ("OpenAI", "Gemini"):
                values = ["low", "medium", "high"]
            else:
                values = list(EFFORT_LEVELS)
            self._thinking_strength_combo["values"] = values
            if self._thinking_strength_var.get() not in values:
                self._thinking_strength_var.set(self.thinking_effort if self.thinking_effort in values else "high")
        elif support == "manual":
            values = list(BUDGET_PRESETS.keys())
            self._thinking_strength_combo["values"] = values
            current = self._thinking_strength_var.get()
            if current not in values:
                for k, v in BUDGET_PRESETS.items():
                    if v == self.thinking_budget:
                        self._thinking_strength_var.set(k)
                        break
                else:
                    self._thinking_strength_var.set("8K")

    def _on_thinking_strength_changed(self):
        val = self._thinking_strength_var.get()
        support = self._model_supports_thinking()
        if support == "adaptive":
            self.thinking_effort = val
        elif support == "manual":
            self.thinking_budget = BUDGET_PRESETS.get(val, 8192)
        self._save_last_state()

    def _on_thinking_mode_changed(self):
        """Handle adaptive thinking mode combobox selection."""
        val = self._thinking_mode_var.get()
        mode = val.lower()  # "off", "none", "adaptive", "low", "medium", "high", "max", "xhigh"
        self.thinking_mode = mode
        if mode in ("off", "none"):
            self.thinking_enabled = False
            self._thinking_var.set(False)
            self.thinking_effort = mode  # "off" for Anthropic, "none" for OpenAI
        else:
            self.thinking_enabled = True
            self._thinking_var.set(True)
            if mode == "adaptive":
                self.thinking_effort = "high"  # internal default, but not sent to API
            else:
                self.thinking_effort = mode  # low/medium/high/max/xhigh/minimal
        # Update temp visibility — pack right after mode combo to maintain order
        # (temp_label and temp_spin were already forgotten by _forget_all_model_widgets,
        #  so packing here places them directly after the mode combo)
        if self._has_model_widgets():
            show_temp = False
            if mode in ("off", "none"):
                if self.provider != "OpenAI" or not self._is_gpt5_family():
                    show_temp = True  # non-gpt5 models
                elif mode == "none" and self._gpt5_supports_temp_at_none():
                    show_temp = True  # gpt-5.4+ with effort=none
            if show_temp:
                self._temp_label.pack(side=tk.LEFT, padx=(10, 5))
                self._temp_spin.pack(side=tk.LEFT, padx=(0, 10))
            else:
                self._temp_label.pack_forget()
                self._temp_spin.pack_forget()
        self._save_last_state()

    def _on_verbosity_changed(self):
        self.text_verbosity = self._text_verbosity_var.get().lower()
        self._save_last_state()

    def _update_title(self):
        model_display = self._get_display_name(self.model)
        model_info = f"{self.provider} / {model_display}"
        inst_num = getattr(self, '_instance_num', 1)
        suffix = f" ({inst_num})" if inst_num > 1 else ""
        if self.agent_instruction_name:
            self.root.title(f"My Agent{suffix} — {self.agent_instruction_name}  [{model_info}]")
        else:
            self.root.title(f"My Agent{suffix}  [{model_info}]")

    # ── Instance Management ────────────────────────────────────────────

    @staticmethod
    def _is_pid_alive(pid):
        """Check if a process with the given PID is a running MyAgent.py instance.
        Verifies both that the executable is Python and that its command line
        contains 'MyAgent.py', so other Python processes (VS Code, Claude Code)
        don't falsely hold lock slots."""
        if IS_WINDOWS:
            try:
                kernel32 = ctypes.windll.kernel32
                psapi = ctypes.windll.psapi
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                PROCESS_VM_READ = 0x0010
                handle = kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid
                )
                if not handle:
                    return False
                try:
                    # Get the executable name of the process
                    buf = ctypes.create_unicode_buffer(260)
                    if psapi.GetModuleBaseNameW(handle, None, buf, 260):
                        exe_name = buf.value.lower()
                        if exe_name not in ("python.exe", "pythonw.exe"):
                            return False
                    else:
                        return False
                finally:
                    kernel32.CloseHandle(handle)
                # Verify command line contains MyAgent.py
                try:
                    result = subprocess.run(
                        ["wmic", "process", "where", f"ProcessId={pid}",
                         "get", "CommandLine", "/value"],
                        capture_output=True, text=True, timeout=5,
                        creationflags=0x08000000,  # CREATE_NO_WINDOW
                    )
                    return "MyAgent.py" in result.stdout
                except Exception:
                    # If we can't check command line, accept the exe name match
                    return True
            except Exception:
                return False
        else:
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            # PID exists — verify it belongs to a MyAgent.py process
            try:
                result = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "command="],
                    capture_output=True, text=True, timeout=5,
                )
                return "MyAgent.py" in result.stdout
            except Exception:
                return True

    def _claim_instance_number(self):
        """Claim the lowest available instance number via lock files.
        Each instance writes a lock file containing its PID. Stale locks
        (where the PID no longer exists) are cleaned up automatically."""
        for num in range(1, 100):
            lock_path = f"{AGENT_LOCK_PREFIX}{num}.lock"
            if os.path.exists(lock_path):
                # Check if the owning process is still alive
                try:
                    with open(lock_path, "r") as f:
                        pid = int(f.read().strip())
                    if self._is_pid_alive(pid):
                        continue  # process alive, slot taken
                except (ValueError, OSError):
                    pass  # stale lock, reclaim it
            # Claim this slot
            try:
                with open(lock_path, "w") as f:
                    f.write(str(os.getpid()))
                self._lock_path = lock_path
                return num
            except OSError:
                continue
        # Fallback — shouldn't happen
        self._lock_path = None
        return 1

    def _release_instance_lock(self):
        """Remove this instance's lock file."""
        lock_path = getattr(self, '_lock_path', None)
        if lock_path and os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except OSError:
                pass

    # ── State Persistence ───────────────────────────────────────────────

    @staticmethod
    def _get_macos_display_rects():
        """Return list of (left, top, right, bottom) for each display via CoreGraphics."""
        try:
            cg = ctypes.cdll.LoadLibrary(
                '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')

            class CGPoint(ctypes.Structure):
                _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

            class CGSize(ctypes.Structure):
                _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

            class CGRect(ctypes.Structure):
                _fields_ = [("origin", CGPoint), ("size", CGSize)]

            max_displays = 16
            display_ids = (ctypes.c_uint32 * max_displays)()
            display_count = ctypes.c_uint32()
            cg.CGGetActiveDisplayList(max_displays, display_ids,
                                      ctypes.byref(display_count))

            cg.CGDisplayBounds.restype = CGRect

            rects = []
            for i in range(display_count.value):
                bounds = cg.CGDisplayBounds(display_ids[i])
                l = int(bounds.origin.x)
                t = int(bounds.origin.y)
                r = int(bounds.origin.x + bounds.size.width)
                b = int(bounds.origin.y + bounds.size.height)
                rects.append((l, t, r, b))
            return rects
        except Exception:
            return []

    @staticmethod
    def _get_windows_display_rects():
        """Return list of (left, top, right, bottom) for each display via EnumDisplayMonitors.
        Primary monitor (origin 0,0) is always first."""
        try:
            user32 = ctypes.windll.user32

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            monitors = []
            MONITORENUMPROC = ctypes.WINFUNCTYPE(
                ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                ctypes.POINTER(RECT), ctypes.c_double)

            def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
                r = lprcMonitor[0]
                monitors.append((r.left, r.top, r.right, r.bottom))
                return 1

            user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
            if monitors:
                # Primary monitor has origin (0,0); sort it first, then by position
                monitors.sort(key=lambda r: (r[0] != 0 or r[1] != 0, r[0], r[1]))
                return monitors
        except Exception:
            pass
        return []

    @staticmethod
    def _get_display_rects():
        """Return list of (left, top, right, bottom) for each display. Cross-platform."""
        if IS_WINDOWS:
            return App._get_windows_display_rects()
        return App._get_macos_display_rects()

    @staticmethod
    def _get_virtual_screen_bounds():
        """Return (vx, vy, vw, vh) covering all monitors."""
        if IS_WINDOWS:
            try:
                user32 = ctypes.windll.user32
                vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
                vy = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
                vw = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
                vh = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
                if vw > 0 and vh > 0:
                    return vx, vy, vw, vh
            except Exception:
                pass
        else:
            # macOS: use CoreGraphics
            rects = App._get_macos_display_rects()
            if rects:
                min_x = min(r[0] for r in rects)
                min_y = min(r[1] for r in rects)
                max_x = max(r[2] for r in rects)
                max_y = max(r[3] for r in rects)
                vw = max_x - min_x
                vh = max_y - min_y
                if vw > 0 and vh > 0:
                    return min_x, min_y, vw, vh
        # Fallback: primary monitor only
        return 0, 0, 1920, 1080

    @staticmethod
    def _get_monitor_config_key():
        """Return a string key identifying the current monitor layout.

        Uses EnumDisplayMonitors (Windows) or CoreGraphics (macOS) to capture
        each monitor's bounding rect, producing a stable key like
        '0,0,1920,1080|1920,0,3840,1080'. Different setups (docked vs
        undocked, different monitor arrangements) produce different keys,
        enabling per-configuration geometry persistence.
        """
        if IS_WINDOWS:
            try:
                user32 = ctypes.windll.user32

                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                monitors = []

                # EnumDisplayMonitors callback: BOOL CALLBACK(HMONITOR, HDC, LPRECT, LPARAM)
                MONITORENUMPROC = ctypes.WINFUNCTYPE(
                    ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                    ctypes.POINTER(RECT), ctypes.c_double)

                def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
                    r = lprcMonitor[0]
                    monitors.append((r.left, r.top, r.right, r.bottom))
                    return 1  # continue enumeration

                user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
                if monitors:
                    monitors.sort()
                    return "|".join(f"{l},{t},{r},{b}" for l, t, r, b in monitors)
            except Exception:
                pass
        else:
            # macOS: use CoreGraphics to enumerate displays
            rects = App._get_macos_display_rects()
            if rects:
                rects.sort()
                return "|".join(f"{l},{t},{r},{b}" for l, t, r, b in rects)
        # Fallback: use virtual screen bounds
        vx, vy, vw, vh = App._get_virtual_screen_bounds()
        return f"{vx},{vy},{vx + vw},{vy + vh}"

    @staticmethod
    def _sanitize_geometry(geo, min_w=200, min_h=150):
        """Validate a geometry string against the full virtual desktop (all monitors).

        Rejects windows that are too small or positioned entirely off-screen.
        Returns DEFAULT_GEOMETRY if unusable.
        """
        m = re.match(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', geo)
        if not m:
            return DEFAULT_GEOMETRY
        w, h, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if w < min_w or h < min_h:
            return DEFAULT_GEOMETRY
        # Check against virtual desktop spanning all monitors
        vx, vy, vw, vh = App._get_virtual_screen_bounds()
        visible_margin = 50
        if x + w < vx + visible_margin or x > vx + vw - visible_margin:
            return DEFAULT_GEOMETRY
        if y + h < vy + visible_margin or y > vy + vh - visible_margin:
            return DEFAULT_GEOMETRY
        return geo

    def _save_last_state(self):
        # Read existing state to preserve geometry entries for other monitor configs
        existing = {}
        if os.path.exists(self._state_file):
            try:
                with open(self._state_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        state = {
            "provider": self.provider,
            "last_instruction_name": self.agent_instruction_name,
            "last_model": self.model,
            "temperature": self.temperature,
            "thinking_enabled": self.thinking_enabled,
            "thinking_effort": self.thinking_effort,
            "thinking_budget": self.thinking_budget,
            "thinking_mode": self.thinking_mode,
            "text_verbosity": self.text_verbosity,
        }
        # Build geometry dict for current monitor configuration
        config_key = self._get_monitor_config_key()
        geo_entry = {"geometry": self.root.geometry()}
        if self.instruction_editor_window and self.instruction_editor_window.winfo_exists():
            geo_entry["editor_geometry"] = self.instruction_editor_window.geometry()
        elif hasattr(self, '_last_editor_geometry') and self._last_editor_geometry:
            geo_entry["editor_geometry"] = self._last_editor_geometry
        # Capture live geometry from open dialogs, fall back to cached values
        ps_dlg = getattr(self, '_ps_safety_dialog', None)
        if ps_dlg and ps_dlg.winfo_exists():
            geo_entry["ps_safety_dialog_geometry"] = ps_dlg.geometry()
        elif getattr(self, '_last_ps_safety_geometry', None):
            geo_entry["ps_safety_dialog_geometry"] = self._last_ps_safety_geometry
        prompt_dlg = getattr(self, '_prompt_dialog', None)
        if prompt_dlg and prompt_dlg.winfo_exists():
            geo_entry["prompt_dialog_geometry"] = prompt_dlg.geometry()
        elif getattr(self, '_last_prompt_dialog_geometry', None):
            geo_entry["prompt_dialog_geometry"] = self._last_prompt_dialog_geometry
        confirm_dlg = getattr(self, '_confirm_dialog', None)
        if confirm_dlg and confirm_dlg.winfo_exists():
            geo_entry["confirm_dialog_geometry"] = confirm_dlg.geometry()
        elif getattr(self, '_last_confirm_dialog_geometry', None):
            geo_entry["confirm_dialog_geometry"] = self._last_confirm_dialog_geometry
        # Capture skills dialog geometry if open, otherwise use last saved
        if self.skills_editor_window and self.skills_editor_window.winfo_exists():
            geo_entry["skills_dialog_geometry"] = self.skills_editor_window.geometry()
        elif getattr(self, '_last_skills_dialog_geometry', None):
            geo_entry["skills_dialog_geometry"] = self._last_skills_dialog_geometry
        # Merge into geometries dict (preserves other monitor configs)
        all_geos = existing.get("geometries", {})
        all_geos[config_key] = geo_entry
        state["geometries"] = all_geos
        # Display checkboxes
        state["show_activity"] = self.show_activity.get()
        state["show_thinking"] = self.show_thinking.get()
        state["save_thinking"] = self.save_thinking.get()
        state["debug_enabled"] = self.debug_enabled.get()
        state["tool_calls_enabled"] = self.tool_calls_enabled.get()
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def _load_last_state(self):
        if not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        # Restore instruction (with its images)
        instr_name = state.get("last_instruction_name", "")
        model_restored = False
        if instr_name:
            instructions = self._load_saved_instructions()
            if instr_name in instructions:
                model_restored = self._apply_instruction_entry(instr_name, instructions[instr_name])
        if not model_restored:
            # Fall back to state file's model params (for old instructions or no instruction)
            self._restore_model_params(state, state_file=True)
        # Restore geometries for the current monitor configuration
        config_key = self._get_monitor_config_key()
        all_geos = state.get("geometries", {})
        geo_entry = all_geos.get(config_key)
        if not geo_entry:
            # Backward compat: migrate old flat geometry fields
            if "geometry" in state:
                geo_entry = {k: state[k] for k in ("geometry", "editor_geometry",
                             "prompt_dialog_geometry", "confirm_dialog_geometry",
                             "ps_safety_dialog_geometry",
                             "skills_dialog_geometry") if k in state}
        if geo_entry:
            geo = geo_entry.get("geometry")
            if geo:
                self.root.geometry(self._sanitize_geometry(geo))
            editor_geo = geo_entry.get("editor_geometry")
            if editor_geo:
                self._last_editor_geometry = editor_geo
            prompt_geo = geo_entry.get("prompt_dialog_geometry")
            if prompt_geo:
                self._last_prompt_dialog_geometry = prompt_geo
            confirm_geo = geo_entry.get("confirm_dialog_geometry")
            if confirm_geo:
                self._last_confirm_dialog_geometry = confirm_geo
            ps_safety_geo = geo_entry.get("ps_safety_dialog_geometry")
            if ps_safety_geo:
                self._last_ps_safety_geometry = ps_safety_geo
            skills_geo = geo_entry.get("skills_dialog_geometry")
            if skills_geo:
                self._last_skills_dialog_geometry = skills_geo
        # Restore display checkboxes
        if "show_activity" in state:
            self.show_activity.set(state["show_activity"])
        if "show_thinking" in state:
            self.show_thinking.set(state["show_thinking"])
        if "save_thinking" in state:
            self.save_thinking.set(state["save_thinking"])
        if "debug_enabled" in state:
            self.debug_enabled.set(state["debug_enabled"])
        if "tool_calls_enabled" in state:
            self.tool_calls_enabled.set(state["tool_calls_enabled"])

    def _periodic_save(self):
        try:
            self._save_last_state()
        except Exception:
            pass
        if self.messages:
            try:
                msg_count = len(self.messages)
                if msg_count != getattr(self, '_last_autosaved_msg_count', 0):
                    self._auto_save_on_close()
                    self._last_autosaved_msg_count = msg_count
            except Exception:
                pass
        self.root.after(5000, self._periodic_save)

    def _apply_instruction_entry(self, name, entry):
        """Load an instruction entry into live state. Returns True if model params were restored."""
        self.agent_instruction = entry["text"]
        self.agent_instruction_name = name
        self.pending_images = [
            (img["data"], img["media_type"], img["filename"])
            for img in entry.get("images", [])
        ]
        self.desktop_enabled.set(entry.get("desktop", False))
        self.browser_enabled.set(entry.get("browser", False))
        self.meta_enabled.set(entry.get("meta", False))
        model_restored = "model" in entry
        if model_restored:
            self._restore_model_params(entry)
        self._restore_skill_modes(entry)
        self._disabled_confirm_patterns = set(entry.get("disabled_confirm_patterns", []))
        self._update_title()
        return model_restored

    def _auto_launch(self):
        """Auto-load an instruction by name and start the agent (from -l arg)."""
        name = self._launch_instruction
        instructions = self._load_saved_instructions()
        if name not in instructions:
            messagebox.showerror(
                "Instruction Not Found",
                f"No saved instruction named '{name}'.\n\n"
                f"Available: {', '.join(sorted(instructions)) or '(none)'}",
            )
            return
        self._apply_instruction_entry(name, instructions[name])
        auto_name = f"{name}_{time.strftime('%Y-%m-%d_%H%M%S')}"
        self.chat_name_entry.delete(0, tk.END)
        self.chat_name_entry.insert(0, auto_name)
        self.root.after(200, self._start_agent)

    # ── Agent Instruction Editor ────────────────────────────────────────

    def _load_saved_instructions(self):
        """Load instructions from disk. Each entry is {text: str, images: list}.
        Migrates old string-only entries automatically."""
        if os.path.exists(INSTRUCTIONS_FILE):
            try:
                with open(INSTRUCTIONS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Migrate old format: {name: "text"} → {name: {text: "...", images: []}}
                migrated = False
                for name, entry in list(data.items()):
                    if isinstance(entry, str):
                        data[name] = {"text": entry, "images": []}
                        migrated = True
                    elif isinstance(entry, dict) and "images" not in entry:
                        entry["images"] = []
                        migrated = True
                if migrated:
                    self._save_instructions_to_disk(data)
                return data
            except (json.JSONDecodeError, OSError):
                pass
        instructions = {"Default": {"text": DEFAULT_INSTRUCTION, "images": []}}
        self._save_instructions_to_disk(instructions)
        return instructions

    def _save_instructions_to_disk(self, instructions):
        with open(INSTRUCTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(instructions, f, indent=2, ensure_ascii=False)

    def do_manage_instructions(self, params):
        """CRUD operations on the saved instruction library."""
        action = params.get("action", "")
        name = params.get("name", "")
        instructions = self._load_saved_instructions()

        if action == "list":
            if not instructions:
                return "No saved instructions."
            lines = []
            for n, entry in sorted(instructions.items()):
                provider = entry.get("provider", "Anthropic")
                model = entry.get("model", "")
                desktop = "desktop" if entry.get("desktop") else ""
                browser = "browser" if entry.get("browser") else ""
                meta = "meta" if entry.get("meta") else ""
                flags = " ".join(f for f in [desktop, browser, meta] if f)
                preview = entry.get("text", "")[:100].replace("\n", " ")
                lines.append(f"• {n}  [{provider}/{model}]{' [' + flags + ']' if flags else ''}\n  {preview}...")
            return "\n".join(lines)

        if not name:
            return "Error: 'name' is required for this action."

        if action == "read":
            if name not in instructions:
                return f"Error: Instruction '{name}' not found."
            entry = instructions[name]
            info = {
                "name": name,
                "text": entry.get("text", ""),
                "desktop": entry.get("desktop", False),
                "browser": entry.get("browser", False),
                "meta": entry.get("meta", False),
                "provider": entry.get("provider", "Anthropic"),
                "model": entry.get("model", ""),
                "temperature": entry.get("temperature", 1.0),
                "thinking_enabled": entry.get("thinking_enabled", False),
                "thinking_effort": entry.get("thinking_effort", "medium"),
                "thinking_budget": entry.get("thinking_budget", 8192),
                "thinking_mode": entry.get("thinking_mode", ""),
                "text_verbosity": entry.get("text_verbosity", "medium"),
                "image_count": len(entry.get("images", [])),
                "skill_modes": entry.get("skill_modes", {}),
            }
            return json.dumps(info, indent=2)

        elif action == "create":
            if name in instructions:
                return f"Error: Instruction '{name}' already exists. Use 'update' to modify it."
            text = params.get("text", "")
            if not text:
                return "Error: 'text' is required when creating an instruction."
            entry = {
                "text": text,
                "images": [],
                "desktop": params.get("desktop", False),
                "browser": params.get("browser", False),
                "meta": params.get("meta", False),
                "provider": self.provider,
                "model": self.model,
                "temperature": self.temperature,
                "thinking_enabled": self.thinking_enabled,
                "thinking_effort": self.thinking_effort,
                "thinking_budget": self.thinking_budget,
                "thinking_mode": self.thinking_mode,
                "text_verbosity": self.text_verbosity,
                "skill_modes": params.get("skill_modes",
                               {sn: sd["mode"] for sn, sd in self.skills.items()}),
                "disabled_confirm_patterns": sorted(self._disabled_confirm_patterns),
            }
            instructions[name] = entry
            self._save_instructions_to_disk(instructions)
            return f"Instruction '{name}' created successfully."

        elif action == "update":
            if name not in instructions:
                return f"Error: Instruction '{name}' not found. Use 'create' to add it."
            updatable = ("text", "desktop", "browser", "meta", "skill_modes",
                         "provider", "model", "temperature",
                         "thinking_enabled", "thinking_effort",
                         "thinking_budget", "thinking_mode", "text_verbosity")
            if all(params.get(k) is None for k in updatable):
                return (
                    "Error: At least one of 'text', 'desktop', 'browser', 'meta', "
                    "'skill_modes', 'provider', 'model', 'temperature', "
                    "'thinking_enabled', 'thinking_effort', 'thinking_budget', "
                    "'thinking_mode', or 'text_verbosity' must be provided for update."
                )
            entry = instructions[name]
            for key in ("text", "desktop", "browser", "meta",
                        "provider", "model", "temperature",
                        "thinking_enabled", "thinking_effort",
                        "thinking_budget", "thinking_mode", "text_verbosity"):
                val = params.get(key)
                if val is not None:
                    entry[key] = val
            skill_modes = params.get("skill_modes")
            if skill_modes is not None:
                existing = entry.get("skill_modes", {})
                existing.update(skill_modes)
                entry["skill_modes"] = existing
            self._save_instructions_to_disk(instructions)
            return f"Instruction '{name}' updated successfully."

        elif action == "delete":
            if name not in instructions:
                return f"Error: Instruction '{name}' not found."
            del instructions[name]
            self._save_instructions_to_disk(instructions)
            if self.agent_instruction_name == name:
                self.agent_instruction_name = ""
            return f"Instruction '{name}' deleted."

        return f"Error: Unknown action '{action}'."

    def open_instruction_editor(self):
        if self.instruction_editor_window and self.instruction_editor_window.winfo_exists():
            self.instruction_editor_window.lift()
            return

        win = tk.Toplevel(self.root)
        win.withdraw()  # Hide until geometry is set
        win.title("Agent Instruction Editor")
        if IS_WINDOWS:
            win.transient(self.root)
        win.protocol("WM_DELETE_WINDOW", lambda: self._on_editor_close(win))
        self.instruction_editor_window = win

        # Row 0: Save row
        tk.Label(win, text="Save Instruction", font=("Arial", 10)).grid(
            row=0, column=0, padx=(10, 5), pady=(10, 5), sticky="w"
        )
        self._instr_name_entry = tk.Entry(win, font=("Arial", 10), width=30)
        self._instr_name_entry.grid(row=0, column=1, padx=5, pady=(10, 5), sticky="ew")

        tk.Button(win, text="SAVE", command=self._save_instruction, width=8).grid(
            row=0, column=2, padx=5, pady=(10, 5)
        )
        tk.Button(win, text="DELETE", command=self._delete_instruction, width=8).grid(
            row=0, column=3, padx=5, pady=(10, 5)
        )
        tk.Button(win, text="CLEAR", command=self._clear_instruction_editor, width=8).grid(
            row=0, column=4, padx=(5, 10), pady=(10, 5)
        )

        # Row 1: Load row
        tk.Label(win, text="Load Instruction", font=("Arial", 10)).grid(
            row=1, column=0, padx=(10, 5), pady=5, sticky="w"
        )
        self._instr_combo_var = tk.StringVar()
        self._instr_combo = ttk.Combobox(
            win, textvariable=self._instr_combo_var, state="readonly",
            font=("Arial", 10), width=28
        )
        self._instr_combo.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        self._instr_combo.bind("<<ComboboxSelected>>", self._on_instruction_selected)
        self._refresh_instruction_list()

        # Row 2: Text editor
        self._instr_text = tk.Text(win, wrap=tk.WORD, font=(MONO_FONT, 10))
        self._instr_text.grid(
            row=2, column=0, columnspan=5, sticky="nsew", padx=10, pady=(5, 5)
        )
        instr_scrollbar = tk.Scrollbar(win, command=self._instr_text.yview)
        instr_scrollbar.grid(row=2, column=5, sticky="ns", pady=(5, 5), padx=(0, 5))
        self._instr_text.config(yscrollcommand=instr_scrollbar.set)

        # Row 3: Model / provider controls
        model_frame = tk.Frame(win)
        model_frame.grid(row=3, column=0, columnspan=6, sticky="ew", padx=10, pady=(5, 0))

        available_providers = [p for p in PROVIDERS
                               if (p == "Anthropic" and self._has_anthropic)
                               or (p == "OpenAI" and self._has_openai)
                               or (p == "Gemini" and self._has_gemini)]
        self._provider_combo = ttk.Combobox(
            model_frame, textvariable=self._provider_var, state="readonly",
            font=("Arial", 9), width=10, values=available_providers,
        )
        self._provider_combo.pack(side=tk.LEFT, padx=(0, 10))
        self._provider_combo.bind("<<ComboboxSelected>>", self._on_provider_changed)

        tk.Label(model_frame, text="Model", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        display_names = [self._get_display_name(mid) for mid in self._model_id_list]
        self._model_combo = ttk.Combobox(
            model_frame, textvariable=self._model_var, state="readonly",
            font=("Arial", 9), width=28
        )
        self._model_combo["values"] = display_names
        self._model_combo.pack(side=tk.LEFT, padx=(0, 10))
        self._model_combo.bind("<<ComboboxSelected>>", self._on_model_selected)

        self._temp_label = tk.Label(model_frame, text="Temp", font=("Arial", 10))
        self._temp_label.pack(side=tk.LEFT, padx=(10, 5))
        self._temp_spin = tk.Spinbox(
            model_frame, textvariable=self._temp_var,
            from_=0.0, to=1.0, increment=0.1,
            width=5, font=("Arial", 10), format="%.1f",
            command=self._on_temp_changed,
        )
        self._temp_spin.pack(side=tk.LEFT, padx=(0, 10))
        self._temp_spin.bind("<Return>", lambda e: self._on_temp_changed())
        self._temp_spin.bind("<FocusOut>", lambda e: self._on_temp_changed())

        self._thinking_check = tk.Checkbutton(
            model_frame, text="Thinking", variable=self._thinking_var,
            font=("Arial", 10), command=self._on_thinking_toggled,
        )
        self._thinking_check.pack(side=tk.LEFT, padx=(10, 2))

        self._thinking_strength_combo = ttk.Combobox(
            model_frame, textvariable=self._thinking_strength_var, state="disabled",
            font=("Arial", 9), width=6,
        )
        self._thinking_strength_combo.pack(side=tk.LEFT, padx=(0, 10))
        self._thinking_strength_combo.bind("<<ComboboxSelected>>", lambda e: self._on_thinking_strength_changed())

        # Adaptive thinking mode combobox (replaces checkbox + strength for adaptive models)
        self._thinking_mode_label = tk.Label(model_frame, text="Thinking", font=("Arial", 10))
        self._thinking_mode_label.pack(side=tk.LEFT, padx=(10, 5))
        self._thinking_mode_combo = ttk.Combobox(
            model_frame, textvariable=self._thinking_mode_var, state="readonly",
            font=("Arial", 9), width=8,
        )
        self._thinking_mode_combo["values"] = ADAPTIVE_MODE_VALUES
        self._thinking_mode_combo.pack(side=tk.LEFT, padx=(0, 10))
        self._thinking_mode_combo.bind("<<ComboboxSelected>>", lambda e: self._on_thinking_mode_changed())

        # Text verbosity combobox (gpt-5 family only)
        self._verbosity_label = tk.Label(model_frame, text="Verbosity", font=("Arial", 10))
        self._verbosity_label.pack(side=tk.LEFT, padx=(10, 5))
        self._verbosity_combo = ttk.Combobox(
            model_frame, textvariable=self._text_verbosity_var, state="readonly",
            font=("Arial", 9), width=7, values=["Low", "Medium", "High"],
        )
        self._verbosity_combo.pack(side=tk.LEFT, padx=(0, 10))
        self._verbosity_combo.bind("<<ComboboxSelected>>", lambda e: self._on_verbosity_changed())

        # Sync adaptive thinking mode var from state before applying widget states
        if self.thinking_mode == "off":
            self._thinking_mode_var.set("Off")
        elif self.thinking_mode == "none":
            self._thinking_mode_var.set("None")
        else:
            self._thinking_mode_var.set(self.thinking_mode.capitalize())

        # Apply current thinking/temp widget states
        self._on_model_selected()

        # Row 4: Image management + tool toggles
        img_frame = tk.Frame(win)
        img_frame.grid(row=4, column=0, columnspan=6, sticky="ew", padx=10, pady=(5, 0))

        tk.Button(
            img_frame, text="Attach Images", command=self.attach_image, width=14
        ).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(
            img_frame, text="Remove Selected", command=self._remove_selected_images, width=16
        ).pack(side=tk.LEFT, padx=(0, 5))

        self._editor_desktop = tk.BooleanVar(value=self.desktop_enabled.get() if _HAS_DESKTOP else False)
        self._editor_browser = tk.BooleanVar(value=self.browser_enabled.get())
        self._editor_meta = tk.BooleanVar(value=self.meta_enabled.get())
        _desktop_cb = tk.Checkbutton(
            img_frame, text="Desktop", variable=self._editor_desktop,
            font=("Arial", 9),
        )
        _desktop_cb.pack(side=tk.LEFT, padx=(15, 0))
        if not _HAS_DESKTOP:
            _desktop_cb.config(state=tk.DISABLED)
        tk.Checkbutton(
            img_frame, text="Browser", variable=self._editor_browser,
            font=("Arial", 9),
        ).pack(side=tk.LEFT, padx=(5, 0))
        tk.Checkbutton(
            img_frame, text="Meta", variable=self._editor_meta,
            font=("Arial", 9),
        ).pack(side=tk.LEFT, padx=(5, 0))

        self.skills_button = tk.Button(
            img_frame, text="Skills", command=self.open_skills_editor, padx=10
        )
        self.skills_button.pack(side=tk.LEFT, padx=(15, 0))
        self._update_skills_button()

        _safety_label = "PS Safety" if IS_WINDOWS else "Shell Safety"
        self.ps_safety_button = tk.Button(
            img_frame, text=_safety_label, command=self._open_ps_safety_dialog, padx=10
        )
        self.ps_safety_button.pack(side=tk.LEFT, padx=(5, 0))
        self._update_ps_safety_button()

        self._instr_image_listbox = tk.Listbox(
            win, height=4, font=("Arial", 9), foreground="#6a1b9a",
            selectmode=tk.EXTENDED,
        )
        self._instr_image_listbox.grid(
            row=5, column=0, columnspan=5, sticky="ew", padx=10, pady=(3, 5)
        )
        img_list_scrollbar = tk.Scrollbar(win, command=self._instr_image_listbox.yview)
        img_list_scrollbar.grid(row=5, column=5, sticky="ns", pady=(3, 5), padx=(0, 5))
        self._instr_image_listbox.config(yscrollcommand=img_list_scrollbar.set)

        # Row 6: Apply button
        tk.Button(
            win, text="Apply", command=self._apply_instruction,
            font=("Arial", 10, "bold"), width=16
        ).grid(row=6, column=0, columnspan=5, pady=(5, 10))

        # Grid weights
        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(2, weight=1)

        # Work on a copy of images so closing without Apply discards changes
        self._editor_images = list(self.pending_images)

        # Load current instruction into editor
        self._instr_text.insert("1.0", self.agent_instruction)
        if self.agent_instruction_name:
            self._instr_name_entry.insert(0, self.agent_instruction_name)
            self._instr_combo_var.set(self.agent_instruction_name)
        self._refresh_image_listbox()

        # Restore geometry AFTER all content is laid out, then show
        win.update_idletasks()
        editor_geo = getattr(self, '_last_editor_geometry', None)
        if editor_geo:
            win.geometry(self._sanitize_geometry(editor_geo, min_w=400, min_h=300))
        else:
            win.geometry("700x640")
        win.deiconify()

    def _nullify_editor_widgets(self):
        """Clear editor widget references so _has_model_widgets() returns False."""
        self._provider_combo = None
        self._model_combo = None
        self._temp_label = None
        self._temp_spin = None
        self._thinking_check = None
        self._thinking_strength_combo = None
        self._thinking_mode_combo = None
        self._thinking_mode_label = None
        self._verbosity_label = None
        self._verbosity_combo = None
        self.ps_safety_button = None

    def _capture_editor_geometry(self):
        """Save editor window geometry for restore on next open."""
        try:
            self._last_editor_geometry = self.instruction_editor_window.geometry()
        except Exception:
            pass

    def _close_editor(self):
        """Capture geometry, destroy the editor, and nullify widget refs."""
        self._capture_editor_geometry()
        # Capture PS Safety dialog geometry before editor destroy cascades to it
        ps_dlg = getattr(self, '_ps_safety_dialog', None)
        if ps_dlg and ps_dlg.winfo_exists():
            try:
                self._last_ps_safety_geometry = ps_dlg.geometry()
            except Exception:
                pass
        self.instruction_editor_window.destroy()
        self._ps_safety_dialog = None
        self._nullify_editor_widgets()

    def _on_editor_close(self, win):
        """Handle editor [X] close."""
        self._close_editor()
        try:
            self._save_last_state()
        except Exception:
            pass

    def _refresh_instruction_list(self):
        instructions = self._load_saved_instructions()
        self._instr_combo["values"] = list(instructions.keys())

    def _save_instruction(self):
        name = self._instr_name_entry.get().strip()
        if not name:
            messagebox.showwarning("No name", "Enter a name for the instruction.", parent=self.instruction_editor_window)
            return
        text = self._instr_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("Empty", "The instruction text is empty.", parent=self.instruction_editor_window)
            return
        # Commit editor state to live
        self.pending_images = list(self._editor_images)
        self.desktop_enabled.set(self._editor_desktop.get())
        self.browser_enabled.set(self._editor_browser.get())
        self.meta_enabled.set(self._editor_meta.get())
        self.agent_instruction = text
        self.agent_instruction_name = name
        # Persist to disk
        instructions = self._load_saved_instructions()
        instructions[name] = {
            "text": text,
            "images": [
                {"data": d, "media_type": mt, "filename": fn}
                for d, mt, fn in self.pending_images
            ],
            "desktop": self.desktop_enabled.get(),
            "browser": self.browser_enabled.get(),
            "meta": self.meta_enabled.get(),
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "thinking_enabled": self.thinking_enabled,
            "thinking_effort": self.thinking_effort,
            "thinking_budget": self.thinking_budget,
            "thinking_mode": self.thinking_mode,
            "text_verbosity": self.text_verbosity,
            "skill_modes": {sn: sk["mode"] for sn, sk in self.skills.items()},
            "disabled_confirm_patterns": sorted(self._disabled_confirm_patterns),
        }
        self._save_instructions_to_disk(instructions)
        self._refresh_instruction_list()
        self._instr_combo_var.set(name)
        self._update_title()
        self._save_last_state()

    def _delete_instruction(self):
        name = self._instr_combo_var.get()
        if not name:
            name = self._instr_name_entry.get().strip()
        if not name:
            messagebox.showwarning("No selection", "Select or enter an instruction name to delete.", parent=self.instruction_editor_window)
            return
        instructions = self._load_saved_instructions()
        if name not in instructions:
            messagebox.showwarning("Not found", f"No saved instruction named '{name}'.", parent=self.instruction_editor_window)
            return
        instructions.pop(name)
        self._save_instructions_to_disk(instructions)
        self._refresh_instruction_list()
        self._instr_combo_var.set("")
        self._instr_name_entry.delete(0, tk.END)

    def _clear_instruction_editor(self):
        self._instr_text.delete("1.0", tk.END)
        self._instr_name_entry.delete(0, tk.END)
        self._instr_combo_var.set("")
        self._editor_images.clear()
        self._editor_desktop.set(False)
        self._editor_browser.set(False)
        self._editor_meta.set(False)
        self._disabled_confirm_patterns = set()
        self._update_ps_safety_button()
        # Reset model controls to defaults
        if self._has_anthropic:
            default_provider = "Anthropic"
            default_model = DEFAULT_MODEL
        elif self._has_openai:
            default_provider = "OpenAI"
            default_model = OPENAI_DEFAULT_MODEL
        else:
            default_provider = "Gemini"
            default_model = GEMINI_DEFAULT_MODEL
        self._provider_var.set(default_provider)
        if default_provider != self.provider:
            self._on_provider_changed()
        else:
            self._model_var.set(self._get_display_name(default_model))
            self._on_model_selected()
        self._temp_var.set(1.0)
        self._on_temp_changed()
        self._thinking_var.set(False)
        self.thinking_mode = "off"
        self._thinking_mode_var.set("Off")
        self._on_thinking_toggled()
        self.text_verbosity = "medium"
        self._text_verbosity_var.set("Medium")
        self._refresh_image_listbox()

    def _on_instruction_selected(self, event):
        name = self._instr_combo_var.get()
        instructions = self._load_saved_instructions()
        if name in instructions:
            entry = instructions[name]
            self._instr_text.delete("1.0", tk.END)
            self._instr_text.insert("1.0", entry["text"])
            self._instr_name_entry.delete(0, tk.END)
            self._instr_name_entry.insert(0, name)
            # Load this instruction's saved images and tool toggles into editor
            self._editor_images = [
                (img["data"], img["media_type"], img["filename"])
                for img in entry.get("images", [])
            ]
            self._editor_desktop.set(entry.get("desktop", False))
            self._editor_browser.set(entry.get("browser", False))
            self._editor_meta.set(entry.get("meta", False))
            self._restore_model_params(entry)
            self._restore_skill_modes(entry)
            self._disabled_confirm_patterns = set(entry.get("disabled_confirm_patterns", []))
            self._update_ps_safety_button()
            self._refresh_image_listbox()

    def _apply_instruction(self):
        text = self._instr_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("Empty", "The instruction text is empty.", parent=self.instruction_editor_window)
            return
        # Commit editor state to live (no disk write)
        self.pending_images = list(self._editor_images)
        self.desktop_enabled.set(self._editor_desktop.get())
        self.browser_enabled.set(self._editor_browser.get())
        self.meta_enabled.set(self._editor_meta.get())
        self.agent_instruction = text
        self.agent_instruction_name = self._instr_name_entry.get().strip()
        # Restore skill modes from whichever instruction is loaded in editor
        instructions = self._load_saved_instructions()
        instr_name = self.agent_instruction_name
        if instr_name and instr_name in instructions:
            self._restore_skill_modes(instructions[instr_name])
        self._update_title()
        self._save_last_state()
        self._close_editor()

    # ── Skills System ───────────────────────────────────────────────────

    def _load_skills(self):
        if os.path.exists(SKILLS_FILE):
            try:
                with open(SKILLS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                migrated = False
                for name, sdata in data.items():
                    if "mode" not in sdata:
                        sdata["mode"] = "enabled" if sdata.pop("enabled", False) else "disabled"
                        migrated = True
                if migrated:
                    with open(SKILLS_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                return data
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_skills(self):
        with open(SKILLS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.skills, f, indent=2, ensure_ascii=False)

    def do_manage_skills(self, params):
        """CRUD operations on the shared skills library."""
        action = params.get("action", "")
        name = params.get("name", "")

        if action == "list":
            if not self.skills:
                return "No skills defined."
            lines = []
            for sn, sd in sorted(self.skills.items()):
                mode = sd.get("mode", "disabled")
                preview = sd.get("content", "")[:100].replace("\n", " ")
                lines.append(f"• {sn}  [{mode}]\n  {preview}...")
            return "\n".join(lines)

        if not name:
            return "Error: 'name' is required for this action."

        if action == "read":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            sd = self.skills[name]
            return json.dumps({"name": name, "content": sd.get("content", ""), "mode": sd.get("mode", "disabled")}, indent=2)

        elif action == "create":
            if name in self.skills:
                return f"Error: Skill '{name}' already exists. Use 'update' to modify it."
            content = params.get("content", "")
            if not content:
                return "Error: 'content' is required when creating a skill."
            mode = params.get("mode", "disabled")
            if mode not in ("disabled", "enabled", "on_demand"):
                return f"Error: Invalid mode '{mode}'. Valid modes: disabled, enabled, on_demand."
            self.skills[name] = {"content": content, "mode": mode}
            self._save_skills()
            self._post_skill_ui_refresh()
            return f"Skill '{name}' created successfully."

        elif action == "update":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found. Use 'create' to add it."
            content = params.get("content")
            mode = params.get("mode")
            if content is None and mode is None:
                return "Error: At least one of 'content' or 'mode' must be provided for update."
            if mode is not None and mode not in ("disabled", "enabled", "on_demand"):
                return f"Error: Invalid mode '{mode}'. Valid modes: disabled, enabled, on_demand."
            if content is not None:
                self.skills[name]["content"] = content
            if mode is not None:
                self.skills[name]["mode"] = mode
            self._save_skills()
            self._post_skill_ui_refresh()
            return f"Skill '{name}' updated successfully."

        elif action == "delete":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            del self.skills[name]
            self._save_skills()
            self._post_skill_ui_refresh()
            return f"Skill '{name}' deleted."

        return f"Error: Unknown action '{action}'."

    def do_run_instruction(self, params):
        """Launch a saved instruction as a separate MyAgent process."""
        name = params.get("name", "")
        headless = params.get("headless", True)

        if not name:
            return "Error: 'name' is required."

        # Verify instruction exists
        instructions = self._load_saved_instructions()
        if name not in instructions:
            available = ", ".join(sorted(instructions.keys())) if instructions else "(none)"
            return f"Error: Instruction '{name}' not found. Available: {available}"

        # Build command to launch a new MyAgent process
        script_path = os.path.join(_BASE_DIR, "MyAgent.py")
        cmd = [sys.executable, script_path, "-l", name]
        if headless:
            cmd.append("--headless")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=_BASE_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            mode = "headless" if headless else "GUI"
            return f"Launched instruction '{name}' in {mode} mode (PID {proc.pid})."
        except Exception as e:
            return f"Error launching instruction '{name}': {e}"

    def _post_skill_ui_refresh(self):
        """Thread-safe refresh of Skills button and Skills Manager listbox."""
        def _refresh():
            self._update_skills_button()
            if (self.skills_editor_window and self.skills_editor_window.winfo_exists()
                    and self._skills_refresh_list):
                self._skills_refresh_list()
        self.root.after(0, _refresh)

    def _restore_skill_modes(self, entry):
        saved = entry.get("skill_modes", {})
        if not saved:
            return
        for sname in self.skills:
            mode = saved.get(sname, "disabled")  # new skills default to disabled
            if mode in ("disabled", "enabled", "on_demand"):
                self.skills[sname]["mode"] = mode
        self._save_skills()
        self._update_skills_button()
        # Refresh Skills Manager listbox if open
        if (self.skills_editor_window and self.skills_editor_window.winfo_exists()
                and self._skills_refresh_list):
            self._skills_refresh_list()

    def _update_skills_button(self):
        on_count = sum(1 for s in self.skills.values() if s.get("mode") == "enabled")
        od_count = sum(1 for s in self.skills.values() if s.get("mode") == "on_demand")
        if on_count and od_count:
            label = f"Skills ({on_count}+{od_count})"
        elif on_count:
            label = f"Skills ({on_count})"
        elif od_count:
            label = f"Skills (0+{od_count})"
        else:
            label = "Skills"
        try:
            self.skills_button.config(text=label)
        except (AttributeError, tk.TclError):
            pass  # Button doesn't exist yet or editor is closed

    def _update_ps_safety_button(self):
        n = len(self._disabled_confirm_patterns)
        base = "PS Safety" if IS_WINDOWS else "Shell Safety"
        label = f"{base} ({n} bypassed)" if n else base
        try:
            self.ps_safety_button.config(text=label)
        except (AttributeError, tk.TclError):
            pass  # Button doesn't exist yet or editor is closed

    def _build_system_prompt(self):
        parts = [self.system_prompt]
        on_demand_names = []
        for name, skill in self.skills.items():
            if skill.get("mode") == "enabled":
                parts.append(f"## Skill: {name}\n{skill['content']}")
            elif skill.get("mode") == "on_demand":
                on_demand_names.append(name)
        if on_demand_names:
            listing = ", ".join(on_demand_names)
            parts.append(
                f"## On-Demand Skills\n"
                f"The following skills are available via the `get_skill` tool: {listing}\n"
                f"Call `get_skill` with the skill name when you need its content."
            )
        return "\n\n".join(parts)

    # ── OpenAI Translation Helpers ─────────────────────────────────────

    def _get_display_name(self, model_id):
        """Get display name for a model, provider-aware."""
        if self.provider == "OpenAI":
            return self._openai_model_display_names.get(model_id, model_id)
        if self.provider == "Gemini":
            return self._gemini_model_display_names.get(model_id, model_id)
        return self._model_display_names.get(model_id, model_id)

    def _tools_to_responses(self, tools):
        """Convert Anthropic tool schemas to OpenAI Responses API format."""
        result = []
        for tool in tools:
            result.append({
                "type": "function",
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                "strict": False,
            })
        return result

    def _messages_to_responses(self, messages):
        """Convert internal Anthropic-format messages to Responses API input format.

        Key differences from Chat Completions:
        - No system message (system prompt moves to 'instructions' parameter)
        - User images use input_text/input_image content types
        - Assistant tool calls become top-level function_call items
        - Tool results become top-level function_call_output items
        """
        result = []

        for msg in messages:
            role = msg["role"]
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    result.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    # Check if this is a tool_result list
                    has_tool_result = any(
                        (isinstance(b, dict) and b.get("type") == "tool_result") for b in content
                    )
                    if has_tool_result:
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tc_content = block.get("content", "")
                                call_id = block.get("tool_use_id", "")
                                # Handle content that is a list (e.g. with image blocks)
                                if isinstance(tc_content, list):
                                    parts = []
                                    for part in tc_content:
                                        if isinstance(part, dict) and part.get("type") == "image":
                                            src = part.get("source", {})
                                            data_url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                                            parts.append({
                                                "type": "input_image",
                                                "image_url": data_url,
                                            })
                                        elif isinstance(part, dict) and part.get("type") == "text":
                                            parts.append({"type": "input_text", "text": part.get("text", "")})
                                        else:
                                            parts.append({"type": "input_text", "text": str(part)})
                                    result.append({
                                        "type": "function_call_output",
                                        "call_id": call_id,
                                        "output": parts,
                                    })
                                else:
                                    result.append({
                                        "type": "function_call_output",
                                        "call_id": call_id,
                                        "output": str(tc_content) if tc_content else "",
                                    })
                    else:
                        # User message with text + images
                        parts = []
                        for block in content:
                            if isinstance(block, dict):
                                if block.get("type") == "text":
                                    parts.append({"type": "input_text", "text": block.get("text", "")})
                                elif block.get("type") == "image":
                                    src = block.get("source", {})
                                    data_url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                                    parts.append({
                                        "type": "input_image",
                                        "image_url": data_url,
                                    })
                            elif isinstance(block, str):
                                parts.append({"type": "input_text", "text": block})
                        result.append({"role": "user", "content": parts})

            elif role == "assistant":
                if isinstance(content, str):
                    result.append({"role": "assistant", "content": [{"type": "output_text", "text": content}]})
                elif isinstance(content, list):
                    # Collect text and tool_use blocks separately
                    text_parts = []
                    func_calls = []
                    for block in content:
                        # Handle both Pydantic objects and dicts
                        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                        if btype == "text":
                            t = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else "")
                            if t:
                                text_parts.append(t)
                        elif btype == "tool_use":
                            bid = getattr(block, "id", None) or (block.get("id") if isinstance(block, dict) else "")
                            bname = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else "")
                            binput = getattr(block, "input", None) or (block.get("input") if isinstance(block, dict) else {})
                            func_calls.append({
                                "type": "function_call",
                                "call_id": bid,
                                "name": bname,
                                "arguments": json.dumps(binput),
                            })
                        # Skip thinking/redacted_thinking blocks
                    combined_text = "\n".join(text_parts)
                    if combined_text:
                        result.append({"role": "assistant", "content": [{"type": "output_text", "text": combined_text}]})
                    # Function calls are top-level items in Responses API
                    result.extend(func_calls)

        return result

    def _stream_responses(self, api_kwargs, label_emitted):
        """Stream an OpenAI Responses API call, accumulating text and tool calls.
        Returns (full_text, stop_reason, content_blocks, had_thinking, label_emitted)."""
        full_text = ""
        had_thinking = False
        tool_calls_acc = {}  # output_index -> {call_id, name, arguments}
        ci_code_acc = ""     # accumulate code interpreter code deltas
        in_thinking = False

        # Waiting ticker & timeout are managed by _stream_responses_call via _oai_first_content
        timed_out = False
        first_content_timeout = getattr(self, '_oai_first_content_timeout', 0)
        with self.openai_client.responses.stream(**api_kwargs) as stream:
            for event in stream:
                # Check stop request — break out of stream immediately
                if self.stop_requested:
                    if hasattr(self, '_oai_first_content'):
                        self._oai_first_content.set()
                    break
                # Check first-content timeout
                if first_content_timeout and hasattr(self, '_oai_first_content') and not self._oai_first_content.is_set():
                    if time.time() - self._oai_stream_start >= first_content_timeout:
                        self._oai_first_content.set()
                        timed_out = True
                        break
                # Reasoning summary deltas (thinking)
                if event.type == "response.reasoning_summary_text.delta":
                    if hasattr(self, '_oai_first_content'):
                        self._oai_first_content.set()
                    if not in_thinking:
                        in_thinking = True
                        had_thinking = True
                        self.queue.put({"type": "thinking_start"})
                    self.queue.put({"type": "thinking_delta", "content": event.delta})

                elif event.type == "response.reasoning_summary_part.done":
                    if in_thinking:
                        self.queue.put({"type": "thinking_end"})
                        in_thinking = False

                # Regular text content
                elif event.type == "response.output_text.delta":
                    if hasattr(self, '_oai_first_content'):
                        self._oai_first_content.set()
                    if in_thinking:
                        self.queue.put({"type": "thinking_end"})
                        in_thinking = False
                    if not label_emitted:
                        self.queue.put({"type": "label"})
                        label_emitted = True
                    full_text += event.delta
                    self.queue.put({"type": "text_delta", "content": event.delta})

                # New output item — capture function call name and call_id
                elif event.type == "response.output_item.added":
                    if hasattr(self, '_oai_first_content'):
                        self._oai_first_content.set()
                    item = getattr(event, "item", None)
                    if item and getattr(item, "type", None) == "function_call":
                        tool_calls_acc[event.output_index] = {
                            "call_id": item.call_id,
                            "name": item.name,
                            "arguments": "",
                        }
                    elif item and getattr(item, "type", None) == "web_search_call":
                        self.queue.put({"type": "tool_info", "content": "Searching the web...\n"})
                    elif item and getattr(item, "type", None) == "code_interpreter_call":
                        self.queue.put({"type": "tool_info", "content": "Running code interpreter...\n"})

                # Function call argument chunks
                elif event.type == "response.function_call_arguments.delta":
                    idx = event.output_index
                    if idx in tool_calls_acc:
                        tool_calls_acc[idx]["arguments"] += event.delta

                # Function call arguments complete
                elif event.type == "response.function_call_arguments.done":
                    idx = event.output_index
                    if idx in tool_calls_acc:
                        tool_calls_acc[idx]["arguments"] = event.arguments

                # Code interpreter — accumulate code deltas
                elif event.type == "response.code_interpreter_call_code.delta":
                    if hasattr(self, '_oai_first_content'):
                        self._oai_first_content.set()
                    ci_code_acc += event.delta

                # Code interpreter — code complete, display full code block
                elif event.type == "response.code_interpreter_call_code.done":
                    if ci_code_acc.strip():
                        self.queue.put({"type": "ci_code", "content": ci_code_acc})
                    ci_code_acc = ""

                # Code interpreter — completed, extract logs and images from outputs
                elif event.type == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item and getattr(item, "type", None) == "code_interpreter_call":
                        outputs = getattr(item, "outputs", []) or []
                        for r in outputs:
                            rtype = getattr(r, "type", None) or (r.get("type") if isinstance(r, dict) else None)
                            if rtype == "logs":
                                logs = getattr(r, "logs", "") or (r.get("logs", "") if isinstance(r, dict) else "")
                                if logs:
                                    self.queue.put({"type": "tool_info", "content": logs + "\n"})
                            elif rtype == "image":
                                # Image URL can be directly on the result or nested under .image
                                url = getattr(r, "url", "") or (r.get("url", "") if isinstance(r, dict) else "")
                                if not url:
                                    img_obj = getattr(r, "image", None) or (r.get("image") if isinstance(r, dict) else None)
                                    if img_obj:
                                        url = getattr(img_obj, "url", "") or (img_obj.get("url", "") if isinstance(img_obj, dict) else "")
                                if url:
                                    self.queue.put({"type": "ci_image", "url": url, "file_id": ""})

        if timed_out:
            raise openai.APITimeoutError(request=None)  # type: ignore[arg-type]

        # End any open thinking block
        if in_thinking:
            self.queue.put({"type": "thinking_end"})

        # Determine stop reason
        stop_reason = "end_turn"
        if tool_calls_acc:
            stop_reason = "tool_use"

        # Build content blocks in Anthropic-like format
        content_blocks = []
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})
        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            try:
                parsed_args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                parsed_args = {"_raw": tc["arguments"]}
            content_blocks.append({
                "type": "tool_use",
                "id": tc["call_id"],
                "name": tc["name"],
                "input": parsed_args,
            })

        return full_text, stop_reason, content_blocks, had_thinking, label_emitted

    def _fetch_openai_models(self):
        """Fetch available OpenAI chat models suitable for agentic tool use."""
        if not self.openai_client:
            return list(OPENAI_FALLBACK_MODELS)
        try:
            response = self.openai_client.models.list()
            model_ids = []
            for m in response.data:
                mid = m.id
                # Skip non-chat model types
                if any(skip in mid for skip in ("embedding", "audio", "search",
                                                "realtime", "preview",
                                                "transcribe", "tts")):
                    continue
                # Include only Responses API compatible models
                if mid.startswith(OPENAI_RESPONSES_PREFIXES):
                    model_ids.append(mid)
            model_ids.sort()
            self._openai_model_display_names = {mid: mid for mid in model_ids}
            return model_ids if model_ids else list(OPENAI_FALLBACK_MODELS)
        except Exception:
            self._openai_model_display_names = {}
            return list(OPENAI_FALLBACK_MODELS)

    def _fetch_gemini_models(self):
        """Fetch available Gemini generative models."""
        if not self.gemini_client:
            return list(GEMINI_FALLBACK_MODELS)
        try:
            model_ids = []
            for m in self.gemini_client.models.list():
                mid = m.name  # e.g. "models/gemini-2.5-flash"
                # Strip "models/" prefix
                if mid.startswith("models/"):
                    mid = mid[len("models/"):]
                # Skip non-generative models
                if any(skip in mid for skip in ("embedding", "imagen", "aqa",
                                                "bisheng", "text-")):
                    continue
                # Skip deprecated models
                if mid.startswith("gemini-2.0-") or mid.startswith("gemini-1."):
                    continue
                model_ids.append(mid)
            model_ids.sort()
            self._gemini_model_display_names = {mid: mid for mid in model_ids}
            return model_ids if model_ids else list(GEMINI_FALLBACK_MODELS)
        except Exception:
            self._gemini_model_display_names = {}
            return list(GEMINI_FALLBACK_MODELS)

    def _fetch_models_for_provider(self):
        """Fetch models for the current provider."""
        if self.provider == "OpenAI":
            return self._fetch_openai_models()
        if self.provider == "Gemini":
            return self._fetch_gemini_models()
        return self._fetch_available_models()

    def _is_openai_reasoning_model(self, model_id=None):
        """Check if the model is an OpenAI reasoning model (o-series or gpt-5+)."""
        mid = model_id or self.model
        # gpt-5.x-chat-* variants are non-reasoning "instant" models
        if "-chat" in mid:
            return False
        return any(mid.startswith(p) for p in OPENAI_REASONING_PREFIXES)

    def _parse_gpt5_minor(self, model_id=None):
        """Parse minor version from gpt-5.x model IDs. Returns 0 for 'gpt-5' base."""
        mid = model_id or self.model
        if mid.startswith("gpt-5."):
            try:
                return int(mid[6:].split('-')[0].split('.')[0])
            except (IndexError, ValueError):
                return 0
        return 0

    def _is_gpt5_family(self, model_id=None):
        """Check if model is in the gpt-5 family (not -chat variants)."""
        mid = model_id or self.model
        return mid.startswith("gpt-5") and "-chat" not in mid

    def _has_reasoning_none(self, model_id=None):
        """Check if model supports reasoning.effort='none' (gpt-5.1+)."""
        mid = model_id or self.model
        return self._is_gpt5_family(mid) and self._parse_gpt5_minor(mid) >= 1

    def _has_reasoning_xhigh(self, model_id=None):
        """Check if model supports reasoning.effort='xhigh'."""
        mid = model_id or self.model
        if not self._is_gpt5_family(mid):
            return False
        if "codex-max" in mid:
            return True
        # mini/nano variants cap at 'high' — no xhigh
        if "-mini" in mid or "-nano" in mid:
            return False
        return self._parse_gpt5_minor(mid) >= 2

    def _gpt5_supports_temp_at_none(self, model_id=None):
        """Check if model supports temperature when reasoning.effort='none' (gpt-5.4+)."""
        mid = model_id or self.model
        return self._is_gpt5_family(mid) and self._parse_gpt5_minor(mid) >= 4

    def _is_gpt5_chat_model(self, model_id=None):
        """Check if model is a gpt-5.x-chat-* Instant variant."""
        mid = model_id or self.model
        return mid.startswith("gpt-5") and "-chat" in mid

    def _has_openai_verbosity(self, model_id=None):
        """Check if model supports text.verbosity (all gpt-5 family including -chat)."""
        mid = model_id or self.model
        return mid.startswith("gpt-5")

    # ── Gemini Translation Helpers ────────────────────────────────────

    def _tools_to_gemini(self, tools):
        """Convert Anthropic tool schemas to Gemini FunctionDeclaration objects."""
        declarations = []
        for tool in tools:
            schema = copy.deepcopy(tool.get("input_schema", {"type": "object", "properties": {}}))
            # Strip additionalProperties which some Gemini models reject
            self._strip_additional_properties(schema)
            declarations.append(genai_types.FunctionDeclaration(
                name=tool["name"],
                description=tool.get("description", ""),
                parameters=schema,
            ))
        return declarations

    def _strip_additional_properties(self, schema):
        """Recursively strip additionalProperties and stringify enum values."""
        if isinstance(schema, dict):
            schema.pop("additionalProperties", None)
            # Gemini only allows enum on STRING type properties
            if "enum" in schema and isinstance(schema["enum"], list):
                schema["enum"] = [str(v) for v in schema["enum"]]
                schema["type"] = "string"
            for v in schema.values():
                self._strip_additional_properties(v)
        elif isinstance(schema, list):
            for item in schema:
                self._strip_additional_properties(item)

    def _messages_to_gemini(self, messages):
        """Convert Anthropic-format messages to Gemini Content objects."""
        contents = []
        # Build tool_use_id → tool_name lookup for function responses
        id_to_name = {}
        for msg in messages:
            if msg["role"] == "assistant" and isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                    if btype == "tool_use":
                        bid = getattr(block, "id", None) or (block.get("id") if isinstance(block, dict) else "")
                        bname = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else "")
                        if bid and bname:
                            id_to_name[bid] = bname

        for msg in messages:
            role = msg["role"]
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    contents.append(genai_types.Content(
                        role="user",
                        parts=[genai_types.Part.from_text(text=content)],
                    ))
                elif isinstance(content, list):
                    # Check if this is a tool_result list
                    has_tool_result = any(
                        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                    )
                    if has_tool_result:
                        parts = []
                        image_parts = []  # Collect images separately
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tool_id = block.get("tool_use_id", "")
                                tool_name = id_to_name.get(tool_id, "unknown")
                                tc_content = block.get("content", "")
                                # Extract text from content (may be string or list)
                                if isinstance(tc_content, list):
                                    text_parts = []
                                    for part in tc_content:
                                        if isinstance(part, dict) and part.get("type") == "text":
                                            text_parts.append(part.get("text", ""))
                                        elif isinstance(part, dict) and part.get("type") == "image":
                                            # Collect images to send in a separate Content
                                            # after function responses — Gemini doesn't
                                            # handle mixed image + function_response Parts
                                            # in the same Content reliably.
                                            src = part.get("source", {})
                                            img_data = base64.b64decode(src.get("data", ""))
                                            media_type = src.get("media_type", "image/png")
                                            image_parts.append(genai_types.Part.from_bytes(
                                                data=img_data, mime_type=media_type,
                                            ))
                                        else:
                                            text_parts.append(str(part))
                                    response_text = "\n".join(text_parts) if text_parts else ""
                                else:
                                    response_text = str(tc_content) if tc_content else ""
                                parts.append(genai_types.Part.from_function_response(
                                    name=tool_name,
                                    response={"result": response_text},
                                ))
                        if parts:
                            contents.append(genai_types.Content(role="user", parts=parts))
                        # Send images in a separate user Content so the model
                        # actually sees them (function_response Content can't
                        # reliably carry inline image Parts in Gemini).
                        if image_parts:
                            # Prefix with a text hint so Gemini associates the
                            # image with the preceding screenshot tool call and
                            # knows to use its pixel coordinates for mouse_click.
                            hint = genai_types.Part.from_text(
                                text=(
                                    "Below is the screenshot image returned by the screenshot tool above. "
                                    "Use the pixel coordinates you see in this image when calling mouse_click "
                                    "— they are automatically scaled to screen coordinates."
                                )
                            )
                            contents.append(genai_types.Content(
                                role="user", parts=[hint] + image_parts,
                            ))
                    else:
                        # User message with text + images
                        parts = []
                        for block in content:
                            if isinstance(block, dict):
                                if block.get("type") == "text":
                                    parts.append(genai_types.Part.from_text(text=block.get("text", "")))
                                elif block.get("type") == "image":
                                    src = block.get("source", {})
                                    img_data = base64.b64decode(src.get("data", ""))
                                    media_type = src.get("media_type", "image/png")
                                    parts.append(genai_types.Part.from_bytes(
                                        data=img_data, mime_type=media_type,
                                    ))
                            elif isinstance(block, str):
                                parts.append(genai_types.Part.from_text(text=block))
                        if parts:
                            contents.append(genai_types.Content(role="user", parts=parts))

            elif role == "assistant":
                if isinstance(content, str):
                    contents.append(genai_types.Content(
                        role="model",
                        parts=[genai_types.Part.from_text(text=content)],
                    ))
                elif isinstance(content, list):
                    parts = []
                    for block in content:
                        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                        if btype == "gemini_thinking":
                            # Reconstruct Gemini thinking part with thought=True
                            t = block.get("text") if isinstance(block, dict) else ""
                            if t:
                                parts.append(genai_types.Part(text=t, thought=True))
                        elif btype == "text":
                            t = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else "")
                            if t:
                                parts.append(genai_types.Part.from_text(text=t))
                        elif btype == "tool_use":
                            bname = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else "")
                            binput = getattr(block, "input", None) or (block.get("input") if isinstance(block, dict) else {})
                            # Include thought_signature if present (required by Gemini thinking models)
                            ts = block.get("thought_signature") if isinstance(block, dict) else getattr(block, "thought_signature", None)
                            if ts:
                                # Ensure bytes — may be base64 string if loaded from saved chat
                                if isinstance(ts, str):
                                    ts = base64.b64decode(ts)
                                parts.append(genai_types.Part(
                                    function_call=genai_types.FunctionCall(name=bname, args=binput),
                                    thought_signature=ts,
                                ))
                            else:
                                parts.append(genai_types.Part.from_function_call(
                                    name=bname, args=binput,
                                ))
                        # Skip thinking/redacted_thinking blocks
                    if parts:
                        contents.append(genai_types.Content(role="model", parts=parts))

        return contents

    def _stream_gemini_call(self, messages, max_retries, label_emitted):
        """Execute one Gemini API call with streaming and retry logic.
        Returns (stop_reason, content_blocks, full_text, had_thinking, label_emitted)."""
        system_prompt = self._build_system_prompt()
        tools = self._get_tools()
        gemini_tools = [genai_types.Tool(function_declarations=self._tools_to_gemini(tools))] if tools else None
        gemini_contents = self._messages_to_gemini(messages)

        # Build config
        config_kwargs = {
            "system_instruction": system_prompt,
            "temperature": self.temperature,
        }
        if self.thinking_enabled and self._is_gemini_thinking_model():
            budget_map = {"minimal": 1024, "low": 1024, "medium": 8192, "high": 24576, "max": 32768}
            budget = budget_map.get(self.thinking_effort, 8192)
            config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                thinking_budget=budget,
            )
        if gemini_tools:
            config_kwargs["tools"] = gemini_tools

        config = genai_types.GenerateContentConfig(**config_kwargs)

        full_text = ""
        had_thinking = False
        thinking_text = ""  # accumulate thinking for message history
        tool_calls = []  # list of {name, id, input}

        for attempt in range(max_retries):
            try:
                in_thinking = False
                tool_index = 0
                response_stream = self.gemini_client.models.generate_content_stream(
                    model=self.model,
                    contents=gemini_contents,
                    config=config,
                )
                for chunk in response_stream:
                    if self.stop_requested:
                        break
                    if not chunk.candidates:
                        continue
                    candidate = chunk.candidates[0]
                    if not candidate.content or not candidate.content.parts:
                        continue
                    for part in candidate.content.parts:
                        if getattr(part, "thought", False):
                            # Thinking part
                            if not in_thinking:
                                in_thinking = True
                                had_thinking = True
                                self.queue.put({"type": "thinking_start"})
                            thinking_text += part.text or ""
                            self.queue.put({"type": "thinking_delta", "content": part.text})
                        elif part.text is not None:
                            # Regular text
                            if in_thinking:
                                self.queue.put({"type": "thinking_end"})
                                in_thinking = False
                            if not label_emitted:
                                self.queue.put({"type": "label"})
                                label_emitted = True
                            full_text += part.text
                            self.queue.put({"type": "text_delta", "content": part.text})
                        elif part.function_call is not None:
                            # Tool call
                            if in_thinking:
                                self.queue.put({"type": "thinking_end"})
                                in_thinking = False
                            fc = part.function_call
                            tool_id = f"gemini_{fc.name}_{tool_index}"
                            tool_index += 1
                            tc_entry = {
                                "name": fc.name,
                                "id": tool_id,
                                "input": dict(fc.args) if fc.args else {},
                            }
                            # Preserve thought_signature for thinking models (required by Gemini API)
                            ts = getattr(part, "thought_signature", None)
                            if ts is not None:
                                # Keep as bytes — the SDK expects bytes when reconstructing Parts
                                tc_entry["thought_signature"] = ts if isinstance(ts, bytes) else ts.encode("utf-8") if isinstance(ts, str) else ts
                            tool_calls.append(tc_entry)
                if in_thinking:
                    self.queue.put({"type": "thinking_end"})
                break  # success
            except Exception as e:
                err_str = str(e)
                status = getattr(e, "code", 0) or getattr(e, "status_code", 0)
                is_timeout = "timed out" in err_str.lower() or "timeout" in err_str.lower() or isinstance(e, (TimeoutError, ConnectionError))
                is_rate_limit = status == 429 or "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                is_server_error = (isinstance(status, int) and status >= 500) or "500" in err_str or "503" in err_str
                if is_timeout and attempt < max_retries - 1:
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Gemini stream timed out — retrying (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    full_text = ""
                    thinking_text = ""
                    tool_calls = []
                elif is_rate_limit and attempt < max_retries - 1:
                    wait = min(2 ** attempt * 5, 60)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Rate limited — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                    full_text = ""
                    thinking_text = ""
                    tool_calls = []
                elif is_server_error and attempt < max_retries - 1:
                    wait = min(2 ** attempt * 10, 90)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"API error — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                    full_text = ""
                    thinking_text = ""
                    tool_calls = []
                else:
                    raise

        # Determine stop reason
        stop_reason = "tool_use" if tool_calls else "end_turn"

        # Build content blocks in Anthropic-like dict format
        content_blocks = []
        # Preserve Gemini thinking parts for message history (required for thought_signature validation)
        if thinking_text:
            content_blocks.append({"type": "gemini_thinking", "text": thinking_text})
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})
        for tc in tool_calls:
            block = {
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["name"],
                "input": tc["input"],
            }
            if "thought_signature" in tc:
                ts = tc["thought_signature"]
                # Store as base64 string for JSON serialization safety
                block["thought_signature"] = base64.b64encode(ts).decode("ascii") if isinstance(ts, bytes) else ts
            content_blocks.append(block)

        return stop_reason, content_blocks, full_text, had_thinking, label_emitted

    def open_skills_editor(self):
        if self.skills_editor_window and self.skills_editor_window.winfo_exists():
            self.skills_editor_window.lift()
            return

        win = tk.Toplevel(self.root)
        win.withdraw()  # Hide until geometry is set
        win.title("Skills Manager")
        parent = (self.instruction_editor_window
                  if self.instruction_editor_window and self.instruction_editor_window.winfo_exists()
                  else self.root)
        if IS_WINDOWS:
            win.transient(parent)
        self.skills_editor_window = win

        def _on_skills_close():
            self._last_skills_dialog_geometry = win.geometry()
            self._save_last_state()
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_skills_close)

        top = tk.Frame(win)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 5))

        tk.Label(top, text="Skill Name", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        name_entry = tk.Entry(top, font=("Arial", 10), width=20)
        name_entry.pack(side=tk.LEFT, padx=(0, 5))

        def save_skill():
            name = name_entry.get().strip()
            if not name:
                messagebox.showwarning("No name", "Enter a name for the skill.", parent=win)
                return
            content = text_editor.get("1.0", "end-1c").strip()
            if not content:
                messagebox.showwarning("Empty", "The skill content is empty.", parent=win)
                return
            mode = self.skills.get(name, {}).get("mode", "disabled")
            self.skills[name] = {"content": content, "mode": mode}
            self._save_skills()
            refresh_list()
            self._update_skills_button()

        def delete_skill():
            sel = skill_listbox.curselection()
            if not sel:
                messagebox.showwarning("No selection", "Select a skill to delete.", parent=win)
                return
            name = skill_listbox.get(sel[0])[5:]
            if name in self.skills:
                del self.skills[name]
                self._save_skills()
                refresh_list()
                name_entry.delete(0, tk.END)
                text_editor.delete("1.0", tk.END)
                self._update_skills_button()

        def new_skill():
            name_entry.delete(0, tk.END)
            text_editor.delete("1.0", tk.END)
            skill_listbox.selection_clear(0, tk.END)

        tk.Button(top, text="SAVE", command=save_skill, width=6).pack(side=tk.LEFT, padx=(5, 2))
        tk.Button(top, text="DELETE", command=delete_skill, width=7).pack(side=tk.LEFT, padx=(2, 2))
        tk.Button(top, text="NEW", command=new_skill, width=5).pack(side=tk.LEFT, padx=(2, 0))

        left = tk.Frame(win)
        left.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=(0, 10))
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        skill_listbox = tk.Listbox(left, font=("Arial", 10), width=20)
        skill_listbox.grid(row=0, column=0, sticky="nsew")
        list_scrollbar = tk.Scrollbar(left, command=skill_listbox.yview)
        list_scrollbar.grid(row=0, column=1, sticky="ns")
        skill_listbox.config(yscrollcommand=list_scrollbar.set)

        toggle_btn = tk.Button(left, text="Cycle Mode", font=("Arial", 9))
        toggle_btn.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))

        def refresh_list():
            skill_listbox.delete(0, tk.END)
            for sname, sdata in self.skills.items():
                mode = sdata.get("mode", "disabled")
                if mode == "enabled":
                    prefix = "[ON] "
                elif mode == "on_demand":
                    prefix = "[OD] "
                else:
                    prefix = "     "
                skill_listbox.insert(tk.END, f"{prefix}{sname}")
            for i, (sname, sdata) in enumerate(self.skills.items()):
                mode = sdata.get("mode", "disabled")
                if mode == "enabled":
                    skill_listbox.itemconfig(i, fg="#2e7d32")
                elif mode == "on_demand":
                    skill_listbox.itemconfig(i, fg="#1565c0")

        self._skills_refresh_list = refresh_list

        def on_select(event):
            sel = skill_listbox.curselection()
            if not sel:
                return
            name = skill_listbox.get(sel[0])[5:]
            if name in self.skills:
                name_entry.delete(0, tk.END)
                name_entry.insert(0, name)
                text_editor.delete("1.0", tk.END)
                text_editor.insert("1.0", self.skills[name]["content"])

        def toggle_skill():
            sel = skill_listbox.curselection()
            if not sel:
                messagebox.showwarning("No selection", "Select a skill to toggle.", parent=win)
                return
            name = skill_listbox.get(sel[0])[5:]
            if name in self.skills:
                cycle = {"disabled": "enabled", "enabled": "on_demand", "on_demand": "disabled"}
                cur = self.skills[name].get("mode", "disabled")
                self.skills[name]["mode"] = cycle.get(cur, "disabled")
                self._save_skills()
                idx = sel[0]
                refresh_list()
                skill_listbox.selection_set(idx)
                skill_listbox.see(idx)
                self._update_skills_button()

        skill_listbox.bind("<<ListboxSelect>>", on_select)
        toggle_btn.config(command=toggle_skill)

        right = tk.Frame(win)
        right.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=(0, 10))
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        text_editor = tk.Text(right, wrap=tk.WORD, font=(MONO_FONT, 10))
        text_editor.grid(row=0, column=0, sticky="nsew")
        text_scrollbar = tk.Scrollbar(right, command=text_editor.yview)
        text_scrollbar.grid(row=0, column=1, sticky="ns")
        text_editor.config(yscrollcommand=text_scrollbar.set)

        win.grid_columnconfigure(0, weight=0)
        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(1, weight=1)

        refresh_list()

        # Restore geometry AFTER all content is laid out, then show
        win.update_idletasks()
        saved_geo = getattr(self, '_last_skills_dialog_geometry', None)
        if saved_geo:
            win.geometry(self._sanitize_geometry(saved_geo, min_w=400, min_h=300))
        else:
            win.geometry("750x500")
        win.deiconify()

    # ── Chat Save ───────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_filename(name, ext='.json'):
        safe = re.sub(r'[<>:"/\\|?*]', '_', name)
        safe = safe.strip('. ')
        return (safe or '_') + ext

    @staticmethod
    def _chat_file_path(name):
        return os.path.join(CHATS_DIR, App._sanitize_filename(name))

    def _save_chat_file(self, name, data):
        os.makedirs(CHATS_DIR, exist_ok=True)
        data['name'] = name
        fpath = self._chat_file_path(name)
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @staticmethod
    def _clean_content_block(block):
        if not isinstance(block, dict):
            return block
        btype = block.get("type")
        if btype == "text":
            return {"type": "text", "text": block.get("text", "")}
        if btype == "tool_use":
            cleaned = {"type": "tool_use", "id": block["id"], "name": block["name"], "input": block["input"]}
            if "thought_signature" in block:
                cleaned["thought_signature"] = block["thought_signature"]
            return cleaned
        if btype == "tool_result":
            cleaned = {"type": "tool_result", "tool_use_id": block["tool_use_id"]}
            if "content" in block:
                content = block["content"]
                if isinstance(content, list):
                    sub_blocks = []
                    for sub in content:
                        if isinstance(sub, dict) and sub.get("type") == "image":
                            sub_blocks.append({"type": "text", "text": "[Screenshot]"})
                        elif isinstance(sub, dict):
                            sub_blocks.append(App._clean_content_block(sub))
                        else:
                            sub_blocks.append(sub)
                    cleaned["content"] = sub_blocks
                else:
                    cleaned["content"] = content
            return cleaned
        if btype == "image":
            return {"type": "text", "text": "[Image was attached]"}
        if btype == "thinking":
            return {"type": "thinking", "thinking": block.get("thinking", ""), "signature": block.get("signature", "")}
        if btype == "redacted_thinking":
            return {"type": "redacted_thinking", "data": block.get("data", "")}
        return block

    def _serialize_messages(self):
        strip_thinking = not self.save_thinking.get()
        serialized = []
        for msg in self.messages:
            content = msg["content"]
            if isinstance(content, str):
                serialized.append({"role": msg["role"], "content": content})
            elif isinstance(content, list):
                blocks = []
                for block in content:
                    if isinstance(block, dict):
                        if strip_thinking and block.get("type") in ("thinking", "redacted_thinking"):
                            continue
                        blocks.append(self._clean_content_block(block))
                    elif hasattr(block, "model_dump"):
                        d = block.model_dump()
                        if strip_thinking and d.get("type") in ("thinking", "redacted_thinking"):
                            continue
                        blocks.append(self._clean_content_block(d))
                    else:
                        blocks.append({"type": "text", "text": str(block)})
                if blocks:
                    serialized.append({"role": msg["role"], "content": blocks})
            else:
                serialized.append({"role": msg["role"], "content": str(content)})
        return serialized

    def _auto_save_on_close(self):
        if not self.messages:
            return
        name = self.chat_name_entry.get().strip()
        if not name:
            return
        self._save_chat_file(name, {
            "messages": self._serialize_messages(),
            "tools": self._get_tools(),
            "system_prompt": self.system_prompt,
            "agent_instruction_name": self.agent_instruction_name,
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "thinking_enabled": self.thinking_enabled,
            "thinking_effort": self.thinking_effort,
            "thinking_budget": self.thinking_budget,
            "thinking_mode": self.thinking_mode,
        })
        txt_path = os.path.join(CHATS_DIR, self._sanitize_filename(name, '.txt'))
        try:
            output_text = self.chat_display.get("1.0", tk.END).rstrip()
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(output_text)
        except Exception:
            pass

    # ── Image Attachment ────────────────────────────────────────────────

    MAX_IMAGE_BYTES = 4_800_000  # stay under Anthropic's 5MB limit

    @staticmethod
    def _compress_image(raw_bytes, max_bytes):
        """Downscale and/or compress an image until it fits under max_bytes.
        Returns (compressed_bytes, media_type)."""
        img = Image.open(io.BytesIO(raw_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        # Try JPEG at decreasing quality first
        for quality in (90, 75, 60, 45, 30):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= max_bytes:
                return buf.getvalue(), "image/jpeg"

        # Still too large — progressively halve dimensions
        for _ in range(5):
            w, h = img.size
            img = img.resize((w // 2, h // 2), Image.LANCZOS)
            for quality in (80, 60, 40):
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality)
                if buf.tell() <= max_bytes:
                    return buf.getvalue(), "image/jpeg"

        # Last resort — return whatever we have
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=30)
        return buf.getvalue(), "image/jpeg"

    def attach_image(self):
        filepaths = filedialog.askopenfilenames(
            title="Select image(s)",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.gif *.webp"),
                ("All files", "*.*"),
            ],
        )
        if not filepaths:
            return
        media_types = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        for filepath in filepaths:
            ext = os.path.splitext(filepath)[1].lower()
            media_type = media_types.get(ext)
            if not media_type:
                messagebox.showwarning("Unsupported format", f"Unsupported image type: {ext}")
                continue
            with open(filepath, "rb") as f:
                raw = f.read()
            # Compress if over the API size limit
            if len(raw) > self.MAX_IMAGE_BYTES:
                raw, media_type = self._compress_image(raw, self.MAX_IMAGE_BYTES)
            image_data = base64.standard_b64encode(raw).decode("utf-8")
            filename = os.path.basename(filepath)
            self._editor_images.append((image_data, media_type, filename))
        self._refresh_image_listbox()

    def _refresh_image_listbox(self):
        """Populate the editor's image listbox from _editor_images."""
        try:
            if (self.instruction_editor_window
                    and self.instruction_editor_window.winfo_exists()
                    and self._instr_image_listbox.winfo_exists()):
                self._instr_image_listbox.delete(0, tk.END)
                for _data, _mt, filename in self._editor_images:
                    self._instr_image_listbox.insert(tk.END, filename)
        except (tk.TclError, AttributeError):
            pass

    def _remove_selected_images(self):
        """Remove images selected in the editor's listbox."""
        try:
            selection = list(self._instr_image_listbox.curselection())
        except (tk.TclError, AttributeError):
            return
        if not selection:
            return
        for idx in reversed(selection):
            del self._editor_images[idx]
        self._refresh_image_listbox()


    # ── Agent Start / Stop ──────────────────────────────────────────────

    def _start_agent(self):
        if self.streaming:
            return
        if not self.agent_instruction.strip():
            messagebox.showwarning("No instruction", "Set an Agent Instruction before starting.")
            return

        # Reset for a new run
        self.messages = []
        self.stop_requested = False
        self.chat_display.config(state="normal")
        self.chat_display.delete("1.0", tk.END)
        self.chat_display.config(state="disabled")

        user_text = self.agent_instruction.strip()

        # Build content with the instruction's images (keep originals for re-runs)
        images = list(self.pending_images)
        if images:
            content = []
            for image_data, media_type, _filename in images:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                })
            content.append({"type": "text", "text": user_text})
            filenames = [img[2] for img in images]
            self.append_message("user", user_text, filenames=filenames)
        else:
            content = user_text
            self.append_message("user", user_text)

        self.messages.append({"role": "user", "content": content})
        self.streaming = True
        self._start_button.config(state="disabled")
        self._stop_button.config(state="normal")
        self.instruction_button.config(state="disabled")

        thread = threading.Thread(
            target=self.stream_worker, args=(self.messages,), daemon=True
        )
        thread.start()

    def _stop_agent(self):
        self.stop_requested = True
        self._stop_button.config(state="disabled")

    def append_message(self, role, content, filenames=None):
        self.chat_display.config(state="normal")
        if role == "user":
            self.chat_display.insert(tk.END, "Instruction:\n", "user_label")
            if filenames:
                for name in filenames:
                    self.chat_display.insert(tk.END, f"[Image: {name}] ", "image_info")
            self.chat_display.insert(tk.END, content + "\n\n", "user")
        self.chat_display.see(tk.END)
        self.chat_display.config(state="disabled")

    # ── Core Tools ──────────────────────────────────────────────────────

    def search_web(self, query):
        try:
            results = DDGS().text(query, max_results=5)
            if not results:
                return "No results found."
            formatted = []
            for r in results:
                formatted.append(f"Title: {r['title']}\nURL: {r['href']}\nSnippet: {r['body']}\n")
            return "\n".join(formatted)
        except Exception as e:
            return f"Search error: {e}"

    def fetch_url(self, url):
        try:
            response = httpx.get(url, follow_redirects=True, timeout=15)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "html" in content_type:
                text = extract_text_from_html(response.text)
            else:
                text = response.text
            if len(text) > 20000:
                text = text[:20000] + "\n\n[Content truncated...]"
            return text
        except Exception as e:
            return f"Error fetching URL: {e}"

    def _open_ps_safety_dialog(self):
        parent = self.instruction_editor_window if (
            self.instruction_editor_window and self.instruction_editor_window.winfo_exists()
        ) else self.root
        dlg = tk.Toplevel(parent)
        self._ps_safety_dialog = dlg
        dlg.withdraw()  # Hide until geometry is set to prevent flicker/repositioning
        dlg.title("PS Safety — Confirm Patterns" if IS_WINDOWS else "Shell Safety — Confirm Patterns")
        if IS_WINDOWS:
            dlg.transient(parent)
        dlg.resizable(True, True)

        tk.Label(
            dlg, text="Checked patterns require confirmation before execution.\n"
                       "Uncheck a pattern to bypass the confirmation dialog.",
            font=("Arial", 9), justify="left",
        ).pack(padx=15, pady=(12, 6), anchor="w")

        # Use a Text widget with embedded checkbuttons for reliable scrolling
        text_frame = tk.Frame(dlg)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 5))
        scrollbar = tk.Scrollbar(text_frame, orient="vertical")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text_widget = tk.Text(
            text_frame, wrap="none", cursor="arrow",
            yscrollcommand=scrollbar.set, highlightthickness=0,
            borderwidth=1, relief="sunken",
        )
        scrollbar.config(command=text_widget.yview)
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        for pattern in COMMAND_CONFIRM:
            var = tk.BooleanVar(value=pattern not in self._disabled_confirm_patterns)
            cb = tk.Checkbutton(
                text_widget, text=pattern, variable=var, font=(MONO_FONT, 9),
                anchor="w", bg="white", activebackground="white",
                command=lambda p=pattern, v=var: self._toggle_confirm_pattern(p, v),
            )
            text_widget.window_create("end", window=cb, stretch=True)
            text_widget.insert("end", "\n")

        text_widget.configure(state="disabled")

        def _on_close():
            self._last_ps_safety_geometry = dlg.geometry()
            self._ps_safety_dialog = None
            self._save_last_state()
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", _on_close)

        # Set geometry AFTER layout but BEFORE showing to prevent WM repositioning
        dlg.update_idletasks()
        saved_geo = getattr(self, '_last_ps_safety_geometry', None)
        if saved_geo:
            geo = self._sanitize_geometry(saved_geo)
        else:
            w, h = 560, 1100
            x = parent.winfo_x() + (parent.winfo_width() - w) // 2
            y = parent.winfo_y() + (parent.winfo_height() - h) // 2
            geo = f"{w}x{h}+{x}+{y}"
        # Apply geometry twice: before and after deiconify, because the embedded
        # checkbuttons in the Text widget request a large natural size that
        # overrides the width/height on map. The delayed re-apply wins.
        # Use after(100ms) instead of after_idle — on macOS the WM repositions
        # transient windows asynchronously after deiconify, so after_idle fires
        # too early and gets overridden.
        dlg.geometry(geo)
        dlg.deiconify()
        dlg.after(100, lambda: dlg.geometry(geo) if dlg.winfo_exists() else None)

    def _toggle_confirm_pattern(self, pattern, var):
        if var.get():
            self._disabled_confirm_patterns.discard(pattern)
        else:
            self._disabled_confirm_patterns.add(pattern)
        self._update_ps_safety_button()
        self._save_last_state()

    def _check_command_safety(self, command):
        for pattern in COMMAND_BLOCKED:
            if re.search(pattern, command, re.IGNORECASE):
                return "blocked", f"BLOCKED: Command matches dangerous pattern ({pattern})"
        for pattern in COMMAND_CONFIRM:
            if re.search(pattern, command, re.IGNORECASE):
                if pattern in self._disabled_confirm_patterns:
                    return "skipped", pattern
                return "confirm", pattern
        return "safe", ""

    def _request_confirmation(self, command, matched_pattern=""):
        event = threading.Event()
        result_holder = [False]

        def ask():
            dlg = tk.Toplevel(self.root)
            self._confirm_dialog = dlg
            dlg.withdraw()  # Hide until geometry is set
            dlg.title("PowerShell — Confirm Command")
            if not self._headless:
                if IS_WINDOWS:
                    dlg.transient(self.root)
                dlg.grab_set()
            else:
                dlg.lift()
                dlg.focus_force()
            dlg.resizable(True, True)

            row = 0
            dlg.grid_columnconfigure(0, weight=1)

            tk.Label(
                dlg, text="The following command requires your approval:",
                font=("Arial", 10), wraplength=450, justify="left",
            ).grid(row=row, column=0, sticky="w", padx=15, pady=(15, 5))
            row += 1

            if matched_pattern:
                tk.Label(
                    dlg, text=f"Triggered by:  {matched_pattern}",
                    font=(MONO_FONT, 9), fg="#cc3300", wraplength=450, justify="left",
                ).grid(row=row, column=0, sticky="w", padx=15, pady=(0, 5))
                row += 1

            dlg.grid_rowconfigure(row, weight=1)
            text_frame = tk.Frame(dlg)
            text_frame.grid(row=row, column=0, sticky="nsew", padx=15, pady=5)
            text_frame.grid_rowconfigure(0, weight=1)
            text_frame.grid_columnconfigure(0, weight=1)
            row += 1

            cmd_text = tk.Text(
                text_frame, wrap=tk.WORD, font=(MONO_FONT, 10),
                relief="sunken", bd=1, height=10,
            )
            cmd_text.grid(row=0, column=0, sticky="nsew")
            cmd_sb = tk.Scrollbar(text_frame, command=cmd_text.yview)
            cmd_sb.grid(row=0, column=1, sticky="ns")
            cmd_text.config(yscrollcommand=cmd_sb.set)
            cmd_text.insert("1.0", command)
            cmd_text.config(state="disabled")

            tk.Label(
                dlg, text="Allow execution?", font=("Arial", 10),
            ).grid(row=row, column=0, pady=(5, 5))
            row += 1

            btn_frame = tk.Frame(dlg)
            btn_frame.grid(row=row, column=0, pady=(0, 15))

            def _capture_geo():
                try:
                    self._last_confirm_dialog_geometry = dlg.geometry()
                except Exception:
                    pass
                self._confirm_dialog = None

            def on_yes():
                result_holder[0] = True
                _capture_geo()
                event.set()
                dlg.destroy()

            def on_no():
                result_holder[0] = False
                _capture_geo()
                event.set()
                dlg.destroy()

            tk.Button(btn_frame, text="Deny", command=on_no, width=10).pack(side=tk.LEFT, padx=10)
            tk.Button(btn_frame, text="Allow", command=on_yes, width=10).pack(side=tk.LEFT, padx=10)

            dlg.protocol("WM_DELETE_WINDOW", on_no)

            # Restore geometry AFTER all content is laid out to prevent layout shifts
            dlg.update_idletasks()
            saved_geo = getattr(self, '_last_confirm_dialog_geometry', None)
            if saved_geo:
                dlg.geometry(self._sanitize_geometry(saved_geo))
            else:
                w = max(dlg.winfo_reqwidth(), 500)
                h = min(dlg.winfo_reqheight(), 400)
                x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
                y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
                dlg.geometry(f"{w}x{h}+{x}+{y}")
            dlg.deiconify()  # Show with correct geometry

        self.root.after(0, ask)
        event.wait()
        return result_holder[0]

    def do_user_prompt(self, message):
        """Pause the agent and ask the user for input via a modal dialog."""
        event = threading.Event()
        result_holder = [""]

        def ask():
            dlg = tk.Toplevel(self.root)
            self._prompt_dialog = dlg
            dlg.withdraw()  # Hide until geometry is set
            dlg.title("Agent Request")
            if not self._headless:
                if IS_WINDOWS:
                    dlg.transient(self.root)
                dlg.grab_set()
            else:
                dlg.lift()
                dlg.focus_force()
            dlg.resizable(True, True)

            dlg.grid_rowconfigure(1, weight=1)
            dlg.grid_rowconfigure(3, weight=1)
            dlg.grid_columnconfigure(0, weight=1)

            tk.Label(
                dlg, text="The agent is requesting your input:",
                font=("Arial", 10), anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=15, pady=(15, 5))

            msg_frame = tk.Frame(dlg)
            msg_frame.grid(row=1, column=0, sticky="nsew", padx=15, pady=5)
            msg_frame.grid_rowconfigure(0, weight=1)
            msg_frame.grid_columnconfigure(0, weight=1)

            msg_text = tk.Text(
                msg_frame, wrap=tk.WORD, font=(MONO_FONT, 10),
                relief="sunken", bd=1, height=6,
            )
            msg_text.grid(row=0, column=0, sticky="nsew")
            msg_sb = tk.Scrollbar(msg_frame, command=msg_text.yview)
            msg_sb.grid(row=0, column=1, sticky="ns")
            msg_text.config(yscrollcommand=msg_sb.set)
            msg_text.insert("1.0", message)
            msg_text.config(state="disabled")

            tk.Label(
                dlg, text="Your response (Ctrl+Enter for newline):", font=("Arial", 10), anchor="w",
            ).grid(row=2, column=0, sticky="w", padx=15, pady=(10, 2))

            resp_frame = tk.Frame(dlg)
            resp_frame.grid(row=3, column=0, sticky="nsew", padx=15, pady=5)
            resp_frame.grid_rowconfigure(0, weight=1)
            resp_frame.grid_columnconfigure(0, weight=1)

            resp_text = tk.Text(
                resp_frame, wrap=tk.WORD, font=(MONO_FONT, 10),
                relief="sunken", bd=1, height=6,
            )
            resp_text.grid(row=0, column=0, sticky="nsew")
            resp_sb = tk.Scrollbar(resp_frame, command=resp_text.yview)
            resp_sb.grid(row=0, column=1, sticky="ns")
            resp_text.config(yscrollcommand=resp_sb.set)

            def _capture_and_close():
                """Save dialog geometry before destroying."""
                try:
                    self._last_prompt_dialog_geometry = dlg.geometry()
                except Exception:
                    pass
                self._prompt_dialog = None

            def on_inject(ev=None):
                result_holder[0] = resp_text.get("1.0", tk.END).strip()
                _capture_and_close()
                event.set()
                dlg.destroy()
                return "break"

            def on_close():
                result_holder[0] = "[User dismissed the dialog without responding]"
                _capture_and_close()
                event.set()
                dlg.destroy()

            def on_newline(ev=None):
                resp_text.insert(tk.INSERT, "\n")
                return "break"

            resp_text.bind("<Return>", on_inject)
            resp_text.bind("<Control-Return>", on_newline)
            resp_text.bind("<Control-KP_Enter>", on_newline)
            dlg.protocol("WM_DELETE_WINDOW", on_close)

            # Restore geometry AFTER all content is laid out to prevent layout shifts
            dlg.update_idletasks()
            saved_geo = getattr(self, '_last_prompt_dialog_geometry', None)
            if saved_geo:
                dlg.geometry(self._sanitize_geometry(saved_geo))
            else:
                w = max(dlg.winfo_reqwidth(), 500)
                h = max(dlg.winfo_reqheight(), 400)
                x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
                y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
                dlg.geometry(f"{w}x{h}+{x}+{y}")
            dlg.deiconify()  # Show with correct geometry

            resp_text.focus_set()

        self.root.after(0, ask)
        event.wait()
        # Echo the user's response in the chat display so it's visible
        response = result_holder[0]
        if response and response != "[User dismissed the dialog without responding]":
            self.queue.put({"type": "user_prompt_echo", "content": response})
        return response

    def run_powershell(self, command):
        safety, info = self._check_command_safety(command)
        if safety == "blocked":
            return info
        if safety == "skipped":
            self.queue.put({"type": "warning", "content": f"\u26a0 Confirm bypassed (pattern: {info})\n"})
        elif safety == "confirm":
            if not self._request_confirmation(command, info):
                return "Command was rejected by the user."
        try:
            shell_cmd = (["powershell", "-NoProfile", "-Command", command]
                         if IS_WINDOWS else ["/bin/bash", "-c", command])
            result = subprocess.run(
                shell_cmd,
                capture_output=True,
                text=True,
                timeout=30,
                **_SUBPROCESS_NOWND,
            )
            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
            if result.returncode != 0:
                output += f"\n[Exit code: {result.returncode}]"
            if len(output) > 20000:
                output = output[:20000] + "\n\n[Output truncated...]"
            return output.strip() if output.strip() else "[No output]"
        except subprocess.TimeoutExpired:
            return "Error: Command timed out after 30 seconds."
        except Exception as e:
            return f"Error running command: {e}"

    # ── CSV Search Tool ─────────────────────────────────────────────────

    def do_csv_search(self, file_path, search_value, column=None, match_mode="contains", max_results=50, delimiter=None):
        try:
            if not os.path.isfile(file_path):
                return f"Error: File not found: {file_path}"
            with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
                if delimiter is None:
                    sample = f.read(8192)
                    f.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=',\t|;')
                        delimiter = dialect.delimiter
                    except csv.Error:
                        delimiter = ','
                elif delimiter == '\\t':
                    delimiter = '\t'
                reader = csv.DictReader(f, delimiter=delimiter)
                headers = reader.fieldnames
                if not headers:
                    return "Error: CSV file has no headers."
                if column and column not in headers:
                    return f"Error: Column '{column}' not found. Available columns: {', '.join(headers)}"
                search_lower = search_value.lower()
                matches = []
                for row_num, row in enumerate(reader, start=2):
                    cells_to_check = [row.get(column, "")] if column else row.values()
                    for cell in cells_to_check:
                        cell_lower = (cell or "").lower()
                        if match_mode == "exact" and cell_lower == search_lower:
                            matched = True
                        elif match_mode == "starts_with" and cell_lower.startswith(search_lower):
                            matched = True
                        elif match_mode == "contains" and search_lower in cell_lower:
                            matched = True
                        else:
                            matched = False
                        if matched:
                            matches.append((row_num, row))
                            break
                    if len(matches) >= max_results:
                        break
            if not matches:
                scope = f"in column '{column}'" if column else "in any column"
                return f"No matches found for '{search_value}' {scope}.\nColumns: {', '.join(headers)}"
            lines = [f"Found {len(matches)} match(es). Columns: {', '.join(headers)}\n"]
            for row_num, row in matches:
                lines.append(f"--- Row {row_num} ---")
                for h in headers:
                    lines.append(f"  {h}: {row.get(h, '')}")
            if len(matches) >= max_results:
                lines.append(f"\n[Results limited to {max_results}. Use max_results to increase.]")
            output = "\n".join(lines)
            if len(output) > 20000:
                output = output[:20000] + "\n\n[Output truncated...]"
            return output
        except UnicodeDecodeError:
            return "Error: File encoding not supported. Expected UTF-8 CSV."
        except Exception as e:
            return f"Error reading CSV: {e}"

    # ── Desktop Automation Tools ────────────────────────────────────────

    KNOWN_APPS = {
        "chrome": "start chrome",
        "firefox": "start firefox",
        "edge": "start msedge",
        "notepad": "notepad",
        "notepad++": "start notepad++",
        "calculator": "calc",
        "calc": "calc",
        "excel": "start excel",
        "word": "start winword",
        "powerpoint": "start powerpnt",
        "explorer": "explorer",
        "cmd": "start cmd",
        "powershell": "start powershell",
        "vscode": "code",
        "code": "code",
        "spotify": "start spotify:",
        "discord": "start discord:",
        "slack": "start slack:",
        "teams": "start msteams:",
    } if IS_WINDOWS else {
        "chrome": "open -a 'Google Chrome'",
        "firefox": "open -a Firefox",
        "edge": "open -a 'Microsoft Edge'",
        "safari": "open -a Safari",
        "calculator": "open -a Calculator",
        "calc": "open -a Calculator",
        "terminal": "open -a Terminal",
        "finder": "open .",
        "explorer": "open .",
        "vscode": "code",
        "code": "code",
        "spotify": "open -a Spotify",
        "discord": "open -a Discord",
        "slack": "open -a Slack",
        "teams": "open -a 'Microsoft Teams'",
    }

    def _macos_display_screenshot(self, display_index=0):
        """Capture a single display on macOS using Quartz CoreGraphics.
        Returns (PIL Image, display_origin_x, display_origin_y) or (None, 0, 0)."""
        try:
            import Quartz
            rects = self._get_macos_display_rects()
            if not rects or display_index >= len(rects):
                return None, 0, 0
            # Use API order: display 0 = primary (origin 0,0)
            l, t, r, b = rects[display_index]
            w, h = r - l, b - t
            # Capture just this display's region
            cg_rect = Quartz.CGRectMake(l, t, w, h)
            cg_img = Quartz.CGWindowListCreateImage(
                cg_rect,
                Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID,
                Quartz.kCGWindowImageDefault,
            )
            if not cg_img:
                return None, 0, 0
            iw = Quartz.CGImageGetWidth(cg_img)
            ih = Quartz.CGImageGetHeight(cg_img)
            cf_data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(cg_img))
            img = Image.frombytes("RGBA", (iw, ih), cf_data, "raw", "BGRA")
            return img.convert("RGB"), l, t
        except Exception:
            return None, 0, 0

    def _capture_single_display(self, display_idx, region=None):
        """Capture a single display (or region), resize to API limit, update scale/offset.
        Returns list of content blocks [text, image] or error string."""
        if region:
            # Convert image coordinates to screen coordinates using current scale/offset
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            rx, ry, rw, rh = int(region[0]), int(region[1]), int(region[2]), int(region[3])
            screen_x = round(rx * scale) + ox
            screen_y = round(ry * scale) + oy
            screen_w = max(round(rw * scale), 1)
            screen_h = max(round(rh * scale), 1)
            if IS_WINDOWS:
                # ImageGrab supports all_screens for multi-monitor; bbox is (l, t, r, b)
                img = ImageGrab.grab(bbox=(screen_x, screen_y,
                                          screen_x + screen_w, screen_y + screen_h),
                                    all_screens=True)
            else:
                img = pyautogui.screenshot(region=(screen_x, screen_y, screen_w, screen_h))
            # Update offset to region origin so subsequent clicks use region-relative coords
            self._screenshot_offset = (screen_x, screen_y)
            # DPI alignment: on macOS Retina, pyautogui returns physical resolution
            phys_w_r, phys_h_r = img.size
            if not IS_WINDOWS and phys_w_r != screen_w and screen_w:
                img = img.resize((screen_w, screen_h))
        elif not IS_WINDOWS:
            img, disp_x, disp_y = self._macos_display_screenshot(display_idx)
            if img is not None:
                self._screenshot_offset = (disp_x, disp_y)
            else:
                img = pyautogui.screenshot()
                self._screenshot_offset = (0, 0)
        else:
            # Windows: per-display capture via ImageGrab with all_screens
            rects = self._get_windows_display_rects()
            if rects and display_idx < len(rects):
                l, t, r, b = rects[display_idx]
                img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
                self._screenshot_offset = (l, t)
            else:
                img = pyautogui.screenshot()
                self._screenshot_offset = (0, 0)
        phys_w, phys_h = img.size
        # Align to logical coordinate space (handles DPI scaling)
        if not region:
            rects = self._get_display_rects()
            if rects:
                idx = min(display_idx, len(rects) - 1)
                l, t, r, b = rects[idx]
                log_w, log_h = r - l, b - t
            else:
                log_w, log_h = pyautogui.size()
            if phys_w != log_w and log_w:
                img = img.resize((log_w, log_h))
        logical_w, logical_h = img.size
        # Resize to API image limit (provider-specific)
        if self.provider == "Gemini":
            max_long_edge, max_megapixels = 2048, 2_000_000
        elif self.provider == "OpenAI":
            max_long_edge, max_megapixels = 2048, 2_000_000
        else:
            max_long_edge, max_megapixels = 1568, 1_150_000
        longest = max(logical_w, logical_h)
        if longest > max_long_edge:
            r = max_long_edge / longest
            max_w = round(logical_w * r)
        else:
            max_w = logical_w
        max_h = round(logical_h * (max_w / logical_w)) if logical_w else logical_h
        if max_w * max_h > max_megapixels:
            r = (max_megapixels / (max_w * max_h)) ** 0.5
            max_w = round(max_w * r)
        if logical_w > max_w:
            ratio = logical_w / max_w
            new_h = round(logical_h / ratio)
            img = img.resize((max_w, new_h))
            self._screenshot_scale = ratio
            img_w, img_h = max_w, new_h
        else:
            self._screenshot_scale = 1.0
            img_w, img_h = logical_w, logical_h
        self._screenshot_dims = (img_w, img_h)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
        return img_w, img_h, b64_data

    def do_screenshot(self, region=None, display=None):
        try:
            if region:
                # Region screenshot on the last-captured display
                img_w, img_h, b64_data = self._capture_single_display(0, region=region)
                return [
                    {"type": "text", "text": f"Region screenshot ({img_w}x{img_h}). Use pixel positions from this image for mouse_click."},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}},
                ]
            # Determine how many displays to capture
            rects = self._get_display_rects()
            num_displays = len(rects) if rects else 1
            if display is not None:
                # Specific display requested
                img_w, img_h, b64_data = self._capture_single_display(display)
                return [
                    {"type": "text", "text": f"Display {display} screenshot ({img_w}x{img_h}). Use pixel positions from this image for mouse_click — coordinates are automatically mapped to the correct screen."},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}},
                ]
            # No display specified — capture ALL displays
            result = []
            for i in range(num_displays):
                img_w, img_h, b64_data = self._capture_single_display(i)
                result.append({"type": "text", "text": f"Display {i} ({img_w}x{img_h}):"})
                result.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}})
            result.append({"type": "text", "text": "To click on a target, first call screenshot with that display number, THEN use mouse_click with coordinates from that specific display's screenshot."})
            # Set offset to primary display (display 0) as default for subsequent clicks
            if num_displays > 1:
                self._capture_single_display(0)  # reset scale/offset to display 0
            return result
        except Exception as e:
            return f"Screenshot error: {e}"

    def do_mouse_click(self, x, y, button="left", clicks=1):
        try:
            x, y = int(x), int(y)
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            iw, ih = self._screenshot_dims
            # Clamp to screenshot image bounds to prevent gross misclicks
            warning = ""
            if iw and ih and (x < 0 or y < 0 or x >= iw or y >= ih):
                warning = f" ⚠ coords ({x},{y}) outside screenshot {iw}x{ih} — clamped"
                x = max(0, min(x, iw - 1))
                y = max(0, min(y, ih - 1))
            screen_x = round(x * scale) + ox
            screen_y = round(y * scale) + oy
            pyautogui.click(screen_x, screen_y, button=button, clicks=clicks)
            return f"Clicked ({button}, {clicks}x) at screen ({screen_x}, {screen_y}) [image ({x},{y}) of {iw}x{ih}, scale {scale:.2f}x, offset ({ox},{oy})]{warning}"
        except Exception as e:
            return f"Click error: {e}"

    def do_type_text(self, text, interval=0.02):
        try:
            if all(ord(c) < 128 for c in text):
                pyautogui.write(text, interval=interval)
            else:
                try:
                    import pyperclip
                except ImportError:
                    return "Error: pyperclip is not installed (needed for non-ASCII text). Install with: pip install pyperclip"
                pyperclip.copy(text)
                paste_mod = "ctrl" if IS_WINDOWS else "command"
                pyautogui.hotkey(paste_mod, "v")
            return f"Typed {len(text)} characters"
        except Exception as e:
            return f"Type error: {e}"

    def do_press_key(self, keys):
        try:
            parts = [k.strip().lower() for k in keys.split("+")]
            # Normalize common aliases (platform-adaptive)
            if IS_WINDOWS:
                aliases = {"windows": "win", "control": "ctrl", "return": "enter", "esc": "escape"}
            else:
                aliases = {"windows": "command", "win": "command", "cmd": "command",
                           "control": "ctrl", "return": "enter", "esc": "escape", "option": "alt"}
            parts = [aliases.get(p, p) for p in parts]
            if len(parts) == 1:
                pyautogui.press(parts[0])
            else:
                pyautogui.hotkey(*parts)
            return f"Pressed: {keys}"
        except Exception as e:
            return f"Key press error: {e}"

    def do_mouse_scroll(self, clicks, x=None, y=None):
        try:
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            kwargs = {}
            if x is not None:
                kwargs["x"] = round(int(x) * scale) + ox
            if y is not None:
                kwargs["y"] = round(int(y) * scale) + oy
            pyautogui.scroll(clicks, **kwargs)
            direction = "up" if clicks > 0 else "down"
            pos = f" at ({x}, {y})" if x is not None else ""
            return f"Scrolled {direction} {abs(clicks)} clicks{pos}"
        except Exception as e:
            return f"Scroll error: {e}"

    def do_open_application(self, name, args=None):
        try:
            key = name.lower().strip()
            if key in self.KNOWN_APPS:
                cmd = self.KNOWN_APPS[key]
            else:
                cmd = name
            if args:
                subprocess.Popen([cmd, args], **_SUBPROCESS_NOWND)
            else:
                subprocess.Popen(cmd, shell=True, **_SUBPROCESS_NOWND)
            return f"Opened {name}{f' with {args}' if args else ''} (command: {cmd})"
        except Exception as e:
            return f"Error opening {name}: {e}"

    def _find_windows_by_title(self, title):
        """Find windows matching title (case-insensitive substring). Cross-platform."""
        pattern = title.lower()
        if IS_WINDOWS:
            matches = gw.getWindowsWithTitle(title)
            return [{"title": w.title, "left": w.left, "top": w.top,
                      "width": w.width, "height": w.height, "_win": w} for w in matches]
        else:
            import Quartz
            wins = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID)
            results = []
            for w in wins:
                app = w.get("kCGWindowOwnerName", "")
                name = w.get("kCGWindowName", "")
                full_title = f"{app} — {name}" if name else app
                if pattern in full_title.lower() or pattern in app.lower() or pattern in (name or "").lower():
                    b = w.get("kCGWindowBounds", {})
                    results.append({"title": full_title,
                                    "left": int(b.get("X", 0)), "top": int(b.get("Y", 0)),
                                    "width": int(b.get("Width", 0)), "height": int(b.get("Height", 0)),
                                    "_app": app, "_pid": w.get("kCGWindowOwnerPID")})
            return results

    def do_find_window(self, title, activate=False):
        try:
            windows = self._find_windows_by_title(title)
            if not windows:
                return f"No windows found matching '{title}'"
            results = []
            for w in windows:
                results.append(f"  Title: {w['title']}\n  Position: ({w['left']}, {w['top']})\n  Size: {w['width']}x{w['height']}")
            if activate and windows:
                try:
                    win = windows[0]
                    if IS_WINDOWS:
                        obj = win["_win"]
                        if obj.isMinimized:
                            obj.restore()
                        obj.activate()
                    else:
                        subprocess.run(["osascript", "-e",
                                        f'tell application "{win["_app"]}" to activate'],
                                       capture_output=True, timeout=5)
                    results.insert(0, f"Activated: {win['title']}")
                except Exception as e:
                    results.insert(0, f"Found but could not activate: {e}")
            return f"Found {len(windows)} window(s):\n" + "\n---\n".join(results)
        except Exception as e:
            return f"Window search error: {e}"

    def do_clipboard_read(self):
        try:
            text = self.root.clipboard_get()
            return f"Clipboard contents:\n{text}"
        except tk.TclError:
            return "Clipboard is empty or contains non-text data."
        except Exception as e:
            return f"Clipboard read error: {e}"

    def do_clipboard_write(self, text):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            preview = text[:100] + "..." if len(text) > 100 else text
            return f"Copied to clipboard ({len(text)} chars): {preview}"
        except Exception as e:
            return f"Clipboard write error: {e}"

    def do_wait_for_window(self, title, timeout=10):
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                windows = self._find_windows_by_title(title)
                if windows:
                    w = windows[0]
                    return (
                        f"Window found: {w['title']}\n"
                        f"Position: ({w['left']}, {w['top']})\n"
                        f"Size: {w['width']}x{w['height']}"
                    )
                time.sleep(0.5)
            return f"Timed out after {timeout}s waiting for window '{title}'"
        except Exception as e:
            return f"Wait for window error: {e}"

    def do_read_screen_text(self, x, y, width, height):
        try:
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            sx = round(int(x) * scale) + ox
            sy = round(int(y) * scale) + oy
            sw = max(round(int(width) * scale), 1)
            sh = max(round(int(height) * scale), 1)
            if IS_WINDOWS:
                img = ImageGrab.grab(bbox=(sx, sy, sx + sw, sy + sh), all_screens=True)
            else:
                img = pyautogui.screenshot(region=(sx, sy, sw, sh))

            if IS_WINDOWS:
                import winocr
                import asyncio
                result = asyncio.run(winocr.recognize_pil(img, lang="en"))
                text = result.text.strip()
            else:
                import objc, Quartz
                Vision = objc.loadBundle("Vision", bundle_path="/System/Library/Frameworks/Vision.framework",
                                         module_globals={})
                from Quartz import CGImageDestinationCreateWithData, CGImageDestinationAddImage
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                ns_data = Quartz.NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
                ci_image = Quartz.CIImage.imageWithData_(ns_data)
                handler = Quartz.VNImageRequestHandler.alloc().initWithCIImage_options_(ci_image, None)
                request = Quartz.VNRecognizeTextRequest.alloc().init()
                request.setRecognitionLevel_(0)  # 0 = accurate
                handler.performRequests_error_([request], None)
                observations = request.results()
                lines = []
                for obs in (observations or []):
                    candidates = obs.topCandidates_(1)
                    if candidates:
                        lines.append(candidates[0].string())
                text = "\n".join(lines).strip()

            if not text:
                return "OCR returned no text for the specified region."
            return f"OCR text from ({x},{y} {width}x{height}):\n{text}"
        except Exception as e:
            return f"OCR error: {e}"

    def do_find_image_on_screen(self, image_path, confidence=0.8):
        try:
            if not os.path.isfile(image_path):
                return f"Image file not found: {image_path}"
            location = pyautogui.locateOnScreen(image_path, confidence=confidence)
            if location is None:
                return f"Image not found on screen (confidence={confidence}): {image_path}"
            cx = location.left + location.width // 2
            cy = location.top + location.height // 2
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            img_cx = round((cx - ox) / scale) if scale else cx
            img_cy = round((cy - oy) / scale) if scale else cy
            return (
                f"Image found at region ({location.left}, {location.top}, "
                f"{location.width}x{location.height})\n"
                f"Center (screen coords): ({cx}, {cy})\n"
                f"Center (image coords for clicking): ({img_cx}, {img_cy})"
            )
        except Exception as e:
            return f"Find image error: {e}"

    def do_mouse_drag(self, start_x, start_y, end_x, end_y, duration=0.5, button="left"):
        try:
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            sx = round(int(start_x) * scale) + ox
            sy = round(int(start_y) * scale) + oy
            ex = round(int(end_x) * scale) + ox
            ey = round(int(end_y) * scale) + oy
            pyautogui.moveTo(sx, sy, duration=0.1)
            pyautogui.mouseDown(button=button)
            pyautogui.moveTo(ex, ey, duration=duration)
            pyautogui.mouseUp(button=button)
            return (
                f"Dragged from ({start_x},{start_y}) to ({end_x},{end_y}) "
                f"with {button} button over {duration}s"
            )
        except Exception as e:
            return f"Mouse drag error: {e}"

    # ── Browser Automation (Playwright via CDP) ─────────────────────────

    def _ensure_browser(self):
        if self._page is not None:
            try:
                self._page.title()
                return self._page
            except Exception:
                self._cleanup_browser()

        def _port_open():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                return s.connect_ex(("127.0.0.1", 9222)) == 0

        if not _port_open():
            if IS_WINDOWS:
                edge_paths = [
                    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
                ]
            else:
                edge_paths = [
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                ]
            edge_exe = None
            for p in edge_paths:
                if os.path.isfile(p):
                    edge_exe = p
                    break
            if not edge_exe:
                raise RuntimeError(
                    "Microsoft Edge not found. Install Edge or check its path." if IS_WINDOWS
                    else "No supported browser found. Install Microsoft Edge or Google Chrome."
                )
            self._edge_process = subprocess.Popen(
                [edge_exe, "--remote-debugging-port=9222"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(30):
                if _port_open():
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError(
                    "Edge launched but debug port 9222 did not open. "
                    "If Edge was already running without --remote-debugging-port, "
                    "close all Edge windows and try again."
                )

        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
        contexts = self._browser.contexts
        if contexts and contexts[0].pages:
            self._page = contexts[0].pages[0]
        else:
            ctx = contexts[0] if contexts else self._browser.new_context()
            self._page = ctx.new_page()
        return self._page

    def _cleanup_browser(self):
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
        self._browser = None
        self._page = None

    def do_browser_open(self, url):
        try:
            self._cleanup_browser()
            if IS_WINDOWS:
                subprocess.run(
                    ["powershell", "-Command", "taskkill /F /IM msedge.exe 2>$null; Start-Sleep -Milliseconds 500"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    **_SUBPROCESS_NOWND,
                )
            else:
                subprocess.run(
                    ["pkill", "-f", "Microsoft Edge"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                time.sleep(0.5)
            page = self._ensure_browser()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return f"Navigated to {url} — page title: {page.title()}"
        except Exception as e:
            return f"Browser open error: {e}"

    def do_browser_navigate(self, url):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return f"Navigated to {url} — page title: {self._page.title()}"
        except Exception as e:
            return f"Browser navigate error: {e}"

    def do_browser_click(self, selector=None, text=None):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            if selector:
                self._page.click(selector, timeout=5000)
                return f"Clicked element: {selector}"
            elif text:
                self._page.get_by_text(text, exact=False).first.click(timeout=5000)
                return f"Clicked element with text: {text}"
            else:
                return "Provide either 'selector' or 'text' to click."
        except Exception as e:
            return f"Browser click error: {e}"

    def do_browser_fill(self, selector, value):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            self._page.fill(selector, value, timeout=5000)
            return f"Filled {selector} with value ({len(value)} chars)"
        except Exception as e:
            return f"Browser fill error: {e}"

    def do_browser_get_text(self, selector=None):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            if selector:
                text = self._page.inner_text(selector, timeout=5000)
            else:
                text = self._page.inner_text("body", timeout=10000)
            if len(text) > 20000:
                text = text[:20000] + "\n\n[Content truncated at 20k chars]"
            return text
        except Exception as e:
            return f"Browser get text error: {e}"

    def do_browser_run_js(self, code):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            stripped = code.strip()
            if stripped.startswith("return "):
                code = f"() => {{ {stripped} }}"
            result = self._page.evaluate(code)
            return json.dumps(result, indent=2, default=str) if result is not None else "[No return value]"
        except Exception as e:
            return f"Browser JS error: {e}"

    def do_browser_screenshot(self):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            raw = self._page.screenshot(type="png")
            img = Image.open(io.BytesIO(raw))
            orig_w, orig_h = img.size
            max_w = 2048
            if orig_w > max_w:
                ratio = orig_w / max_w
                new_h = int(orig_h / ratio)
                img = img.resize((max_w, new_h))
                img_w, img_h = max_w, new_h
            else:
                img_w, img_h = orig_w, orig_h
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64_data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
            return [
                {"type": "text", "text": f"Browser screenshot ({img_w}x{img_h})."},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}},
            ]
        except Exception as e:
            return f"Browser screenshot error: {e}"

    def do_browser_close(self):
        try:
            self._cleanup_browser()
            return "Browser connection closed. Edge is still running."
        except Exception as e:
            return f"Browser close error: {e}"

    def do_browser_wait_for(self, selector, timeout=10000):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            el = self._page.wait_for_selector(selector, timeout=timeout)
            text = el.inner_text() if el else ""
            return f"Element '{selector}' appeared. Text: {text[:500]}"
        except Exception as e:
            return f"Browser wait error: {e}"

    def do_browser_select(self, selector, value=None, label=None):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            if value:
                self._page.select_option(selector, value=value, timeout=5000)
                return f"Selected option with value '{value}' in {selector}"
            elif label:
                self._page.select_option(selector, label=label, timeout=5000)
                return f"Selected option with label '{label}' in {selector}"
            else:
                return "Provide either 'value' or 'label' to select."
        except Exception as e:
            return f"Browser select error: {e}"

    def do_browser_get_elements(self, selector, limit=10):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            js = f"""
            (() => {{
                const els = document.querySelectorAll({json.dumps(selector)});
                const results = [];
                for (let i = 0; i < Math.min(els.length, {limit}); i++) {{
                    const el = els[i];
                    const rect = el.getBoundingClientRect();
                    const attrs = {{}};
                    for (const a of el.attributes) attrs[a.name] = a.value;
                    results.push({{
                        tag: el.tagName.toLowerCase(),
                        text: el.innerText.substring(0, 200),
                        attrs: attrs,
                        visible: rect.width > 0 && rect.height > 0,
                        rect: {{top: Math.round(rect.top), left: Math.round(rect.left),
                                width: Math.round(rect.width), height: Math.round(rect.height)}}
                    }});
                }}
                return results;
            }})()
            """
            results = self._page.evaluate(js)
            if not results:
                return f"No elements found matching '{selector}'"
            lines = [f"Found {len(results)} element(s) matching '{selector}':"]
            for r in results:
                lines.append(
                    f"  <{r['tag']}> text={r['text'][:80]!r}\n"
                    f"      attrs: {r['attrs']}\n"
                    f"      visible: {r['visible']}, rect: {r.get('rect', {})}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"browser_get_elements error: {e}"

    # ── Streaming Engine ────────────────────────────────────────────────

    def _make_serializable(self, obj):
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        return str(obj)

    def _payload_for_display(self, messages):
        display_msgs = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                content = [
                    self._make_serializable(block) if not isinstance(block, dict) else block
                    for block in content
                ]
            display_msgs.append({"role": msg["role"], "content": content})
        display_msgs = copy.deepcopy(display_msgs)

        def _truncate_images(blocks):
            for block in blocks:
                if isinstance(block, dict):
                    if block.get("type") == "image":
                        src = block.get("source", {})
                        if src.get("data"):
                            src["data"] = src["data"][:40] + "...[truncated]"
                    if block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                        _truncate_images(block["content"])

        for msg in display_msgs:
            content = msg.get("content")
            if isinstance(content, list):
                _truncate_images(content)

        if self.provider == "OpenAI":
            system_prompt = self._build_system_prompt()
            tools = self._get_tools()
            responses_tools = self._tools_to_responses(tools) if tools else []
            responses_tools.append({"type": "web_search_preview"})
            responses_tools.append({"type": "code_interpreter", "container": {"type": "auto"}})
            responses_input = self._messages_to_responses(display_msgs)
            # Truncate input_image data in Responses API input
            for item in responses_input:
                c = item.get("content")
                if isinstance(c, list):
                    for part in c:
                        if isinstance(part, dict) and part.get("type") == "input_image":
                            url = part.get("image_url", "")
                            if isinstance(url, str) and url.startswith("data:"):
                                part["image_url"] = url[:60] + "...[truncated]"
                # Also truncate images in function_call_output
                if item.get("type") == "function_call_output" and isinstance(item.get("output"), list):
                    for part in item["output"]:
                        if isinstance(part, dict) and part.get("type") == "input_image":
                            url = part.get("image_url", "")
                            if isinstance(url, str) and url.startswith("data:"):
                                part["image_url"] = url[:60] + "...[truncated]"
            payload = {
                "model": self.model,
                "input": responses_input,
                "instructions": system_prompt,
                "tools": responses_tools,
                "store": False,
                "include": ["code_interpreter_call.outputs"],
            }
            is_reasoning = self._is_openai_reasoning_model()
            if is_reasoning and self.thinking_enabled:
                payload["reasoning"] = {"effort": self.thinking_effort, "summary": "auto"}
            elif not is_reasoning:
                payload["temperature"] = self.temperature
        else:
            tools = self._get_tools()
            if self.provider == "Anthropic":
                tools.append({"type": "web_search_20250305", "name": "web_search"})
                tools.append({"type": "code_execution_20250825", "name": "code_execution"})
            payload = {
                "model": self.model,
                "stream": True,
                "system": self._build_system_prompt(),
                "tools": tools,
                "messages": display_msgs,
            }
            model_cap = MODEL_MAX_OUTPUT_TOKENS.get(self.model)
            if self.thinking_enabled:
                support = self._model_supports_thinking()
                payload["max_tokens"] = min(MAX_TOKENS_THINKING, model_cap) if model_cap else MAX_TOKENS_THINKING
                if support == "adaptive":
                    payload["thinking"] = {"type": "adaptive"}
                    if self.thinking_mode not in ("off", "adaptive"):
                        payload["output_config"] = {"effort": self.thinking_mode}
                elif support == "manual":
                    payload["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
            else:
                payload["max_tokens"] = min(MAX_TOKENS, model_cap) if model_cap else MAX_TOKENS
                payload["temperature"] = self.temperature
        return json.dumps(payload, indent=2)

    def _get_tools(self):
        tools = copy.deepcopy(TOOLS)
        # OpenAI/Anthropic use native server-side web search; exclude custom web tools
        # (Gemini can't combine built-in tools with function calling, so it keeps local tools)
        if self.provider in ("OpenAI", "Anthropic"):
            tools = [t for t in tools if t["name"] not in ("web_search", "fetch_webpage")]
        if self.desktop_enabled.get() and _HAS_DESKTOP:
            desktop = copy.deepcopy(DESKTOP_TOOLS)
            # Build display info for tool description (API order: 0=primary)
            rects = self._get_display_rects()
            num_displays = len(rects) if rects else 1
            if rects:
                disp_info = ", ".join(
                    f"display {i}: {r[2]-r[0]}x{r[3]-r[1]}" for i, r in enumerate(rects)
                )
            else:
                sw, sh = pyautogui.size()
                disp_info = f"display 0: {sw}x{sh}"
            for tool in desktop:
                if tool["name"] == "screenshot":
                    if num_displays > 1:
                        tool["description"] = (
                            f"Take a screenshot. {num_displays} displays available: {disp_info}. "
                            "By default (no 'display' parameter), captures ALL displays as separate images "
                            "so you can see everything. To click on something, call screenshot again with "
                            "the specific 'display' number where the target is, then use coordinates from "
                            "THAT screenshot for mouse_click. Always take a screenshot BEFORE clicking. "
                            "TIP: For precise clicking on small targets (close buttons, icons), take a REGION "
                            "screenshot (x, y, width, height) zoomed into just that area."
                        )
                    else:
                        tool["description"] = (
                            f"Take a screenshot of the screen (resolution {disp_info.split(': ')[1]}). "
                            "Always use this FIRST to see what is on the screen before clicking or typing. "
                            "The image may be resized. For mouse_click, use the pixel coordinates as you see "
                            "them in the image — they are automatically scaled to screen coordinates. "
                            "Optionally capture only a region by specifying x, y, width, height. "
                            "TIP: For precise clicking on small targets (close buttons, icons), first take a "
                            "full screenshot to locate the target, then take a REGION screenshot zoomed into "
                            "just that area for pixel-accurate coordinates."
                        )
                    break
            tools.extend(desktop)
        if self.browser_enabled.get():
            tools.extend(copy.deepcopy(BROWSER_TOOLS))
        if self.meta_enabled.get():
            tools.extend(copy.deepcopy(META_TOOLS))
        od_names = [n for n, s in self.skills.items() if s.get("mode") == "on_demand"]
        if od_names:
            tools.append({
                "name": "get_skill",
                "description": "Retrieve the full content of an on-demand skill by name.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "Name of the skill to retrieve.",
                            "enum": od_names,
                        }
                    },
                    "required": ["skill_name"],
                },
            })
        return tools

    def _execute_tool(self, block):
        """Execute a single tool_use block and return the result.

        Thread-safe for parallel-safe tools (web_search, fetch_webpage,
        csv_search, get_skill). Sequential tools (desktop, browser,
        run_powershell, user_prompt) must only be called from one thread.
        """
        if block.name == "web_search":
            query = block.input.get("query", "")
            self.queue.put({"type": "tool_info", "content": f"Searching: {query}\n"})
            return self.search_web(query)
        elif block.name == "fetch_webpage":
            url = block.input.get("url", "")
            self.queue.put({"type": "tool_info", "content": f"Fetching: {url}\n"})
            return self.fetch_url(url)
        elif block.name == "run_command":
            cmd = block.input.get("command", "")
            self.queue.put({"type": "tool_info", "content": f"Running: {cmd}\n"})
            return self.run_powershell(cmd)
        elif block.name == "csv_search":
            inp = block.input
            fp = inp.get("file_path", "")
            sv = inp.get("search_value", "")
            self.queue.put({"type": "tool_info", "content": f"Searching CSV: {os.path.basename(fp)} for '{sv}'\n"})
            return self.do_csv_search(
                fp, sv,
                column=inp.get("column"),
                match_mode=inp.get("match_mode", "contains"),
                max_results=inp.get("max_results", 50),
                delimiter=inp.get("delimiter"),
            )
        elif block.name == "user_prompt":
            prompt_msg = block.input.get("message", "")
            self.queue.put({"type": "tool_info", "content": "Requesting user input...\n"})
            return self.do_user_prompt(prompt_msg)
        elif block.name in ("screenshot", "mouse_click", "type_text",
                             "press_key", "mouse_scroll", "open_application",
                             "find_window", "clipboard_read", "clipboard_write",
                             "wait_for_window", "read_screen_text",
                             "find_image_on_screen", "mouse_drag"):
            if not self.desktop_enabled.get():
                return "Desktop control is disabled. Enable the Desktop checkbox to use this tool."
            inp = block.input
            if block.name == "screenshot":
                display = inp.get("display")  # None = all displays
                if display is not None:
                    display = int(display)  # Gemini proto returns floats
                disp_label = f"display {display}" if display is not None else "all displays"
                self.queue.put({"type": "tool_info", "content": f"Taking screenshot ({disp_label})...\n"})
                region = None
                if all(k in inp for k in ("x", "y", "width", "height")):
                    region = (inp["x"], inp["y"], inp["width"], inp["height"])
                return self.do_screenshot(region, display=display)
            elif block.name == "mouse_click":
                cx, cy = inp.get("x"), inp.get("y")
                if cx is None or cy is None:
                    coord = inp.get("coordinate")
                    if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                        cx, cy = coord[0], coord[1]
                    else:
                        return f"mouse_click error: missing x/y coordinates. Got: {inp}"
                self.queue.put({"type": "tool_info", "content": f"Clicking at ({cx}, {cy})...\n"})
                return self.do_mouse_click(
                    cx, cy,
                    button=inp.get("button", "left"),
                    clicks=int(inp.get("clicks", 1)),
                )
            elif block.name == "type_text":
                text = inp.get("text", "")
                preview = text[:50] + "..." if len(text) > 50 else text
                self.queue.put({"type": "tool_info", "content": f"Typing: {preview}\n"})
                return self.do_type_text(text, interval=inp.get("interval", 0.02))
            elif block.name == "press_key":
                keys = inp.get("keys", "")
                self.queue.put({"type": "tool_info", "content": f"Pressing: {keys}\n"})
                return self.do_press_key(keys)
            elif block.name == "mouse_scroll":
                clicks_val = int(inp.get("clicks", 0))
                self.queue.put({"type": "tool_info", "content": f"Scrolling {clicks_val} clicks...\n"})
                return self.do_mouse_scroll(clicks_val, x=inp.get("x"), y=inp.get("y"))
            elif block.name == "open_application":
                app_name = inp.get("name", "")
                app_args = inp.get("args")
                self.queue.put({"type": "tool_info", "content": f"Opening: {app_name}{f' {app_args}' if app_args else ''}\n"})
                return self.do_open_application(app_name, args=app_args)
            elif block.name == "find_window":
                title = inp.get("title", "")
                self.queue.put({"type": "tool_info", "content": f"Finding windows: {title}\n"})
                return self.do_find_window(title, activate=inp.get("activate", False))
            elif block.name == "clipboard_read":
                self.queue.put({"type": "tool_info", "content": "Reading clipboard...\n"})
                return self.do_clipboard_read()
            elif block.name == "clipboard_write":
                text = inp.get("text", "")
                preview = text[:50] + "..." if len(text) > 50 else text
                self.queue.put({"type": "tool_info", "content": f"Writing to clipboard: {preview}\n"})
                return self.do_clipboard_write(text)
            elif block.name == "wait_for_window":
                title = inp.get("title", "")
                timeout = inp.get("timeout", 10)
                self.queue.put({"type": "tool_info", "content": f"Waiting for window: {title}\n"})
                return self.do_wait_for_window(title, timeout=timeout)
            elif block.name == "read_screen_text":
                rx, ry, rw, rh = inp.get("x"), inp.get("y"), inp.get("width"), inp.get("height")
                if None in (rx, ry, rw, rh):
                    return f"read_screen_text error: missing region parameters. Got: {inp}"
                self.queue.put({"type": "tool_info", "content": f"OCR region ({rx},{ry} {rw}x{rh})...\n"})
                return self.do_read_screen_text(rx, ry, rw, rh)
            elif block.name == "find_image_on_screen":
                path = inp.get("image_path", "")
                self.queue.put({"type": "tool_info", "content": f"Finding image: {os.path.basename(path)}\n"})
                return self.do_find_image_on_screen(path, confidence=inp.get("confidence", 0.8))
            elif block.name == "mouse_drag":
                sx, sy = inp.get("start_x"), inp.get("start_y")
                ex, ey = inp.get("end_x"), inp.get("end_y")
                if None in (sx, sy, ex, ey):
                    return f"mouse_drag error: missing coordinates. Got: {inp}"
                self.queue.put({"type": "tool_info", "content": f"Dragging ({sx},{sy}) to ({ex},{ey})...\n"})
                return self.do_mouse_drag(
                    sx, sy, ex, ey,
                    duration=inp.get("duration", 0.5),
                    button=inp.get("button", "left"),
                )
        elif block.name in ("browser_open", "browser_navigate",
                              "browser_click", "browser_fill",
                              "browser_get_text", "browser_run_js",
                              "browser_screenshot", "browser_close",
                              "browser_wait_for", "browser_select",
                              "browser_get_elements"):
            if not self.browser_enabled.get():
                return "Browser tools are disabled. Enable the Browser checkbox to use this tool."
            inp = block.input
            if block.name == "browser_open":
                url = inp.get("url", "")
                self.queue.put({"type": "tool_info", "content": f"Browser: opening {url}\n"})
                return self.do_browser_open(url)
            elif block.name == "browser_navigate":
                url = inp.get("url", "")
                self.queue.put({"type": "tool_info", "content": f"Browser: navigating to {url}\n"})
                return self.do_browser_navigate(url)
            elif block.name == "browser_click":
                sel = inp.get("selector", "")
                txt = inp.get("text", "")
                target = sel or f"text='{txt}'"
                self.queue.put({"type": "tool_info", "content": f"Browser: clicking {target}\n"})
                return self.do_browser_click(selector=sel or None, text=txt or None)
            elif block.name == "browser_fill":
                sel = inp.get("selector", "")
                val = inp.get("value", "")
                self.queue.put({"type": "tool_info", "content": f"Browser: filling {sel}\n"})
                return self.do_browser_fill(sel, val)
            elif block.name == "browser_get_text":
                sel = inp.get("selector", "")
                self.queue.put({"type": "tool_info", "content": f"Browser: reading text{' from ' + sel if sel else ''}...\n"})
                return self.do_browser_get_text(selector=sel or None)
            elif block.name == "browser_run_js":
                code = inp.get("code", "")
                preview = code[:80] + "..." if len(code) > 80 else code
                self.queue.put({"type": "tool_info", "content": f"Browser: running JS: {preview}\n"})
                return self.do_browser_run_js(code)
            elif block.name == "browser_screenshot":
                self.queue.put({"type": "tool_info", "content": "Browser: taking screenshot...\n"})
                return self.do_browser_screenshot()
            elif block.name == "browser_close":
                self.queue.put({"type": "tool_info", "content": "Browser: closing connection...\n"})
                return self.do_browser_close()
            elif block.name == "browser_wait_for":
                sel = inp.get("selector", "")
                timeout = inp.get("timeout", 10000)
                self.queue.put({"type": "tool_info", "content": f"Browser: waiting for {sel}...\n"})
                return self.do_browser_wait_for(sel, timeout=timeout)
            elif block.name == "browser_select":
                sel = inp.get("selector", "")
                self.queue.put({"type": "tool_info", "content": f"Browser: selecting in {sel}...\n"})
                return self.do_browser_select(sel, value=inp.get("value"), label=inp.get("label"))
            elif block.name == "browser_get_elements":
                sel = inp.get("selector", "")
                limit = inp.get("limit", 10)
                self.queue.put({"type": "tool_info", "content": f"Browser: getting elements {sel}...\n"})
                return self.do_browser_get_elements(sel, limit=limit)
        elif block.name == "get_skill":
            skill_name = block.input.get("skill_name", "")
            self.queue.put({"type": "tool_info", "content": f"Loading skill: {skill_name}\n"})
            if skill_name in self.skills and self.skills[skill_name].get("mode") == "on_demand":
                return self.skills[skill_name]["content"]
            else:
                return f"Skill not found or not on-demand: {skill_name}"
        elif block.name == "manage_instructions":
            action = block.input.get("action", "")
            self.queue.put({"type": "tool_info", "content": f"manage_instructions: {action}\n"})
            return self.do_manage_instructions(block.input)
        elif block.name == "manage_skills":
            action = block.input.get("action", "")
            self.queue.put({"type": "tool_info", "content": f"manage_skills: {action}\n"})
            return self.do_manage_skills(block.input)
        elif block.name == "run_instruction":
            name = block.input.get("name", "")
            headless = block.input.get("headless", True)
            mode = "headless" if headless else "GUI"
            self.queue.put({"type": "tool_info", "content": f"run_instruction: {name} ({mode})\n"})
            return self.do_run_instruction(block.input)
        else:
            return f"Unknown tool: {block.name}"

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        """Execute one Anthropic API call with streaming and retry logic.
        Returns (stop_reason, content_blocks, full_text, had_thinking, label_emitted)."""
        full_text = ""
        had_thinking = False

        tools = self._get_tools()
        # Add Anthropic server-side tools
        tools.append({"type": "web_search_20250305", "name": "web_search"})
        tools.append({"type": "code_execution_20250825", "name": "code_execution"})
        api_kwargs = {
            "model": self.model,
            "system": self._build_system_prompt(),
            "messages": messages,
            "tools": tools,
        }
        model_cap = MODEL_MAX_OUTPUT_TOKENS.get(self.model)
        if self.thinking_enabled:
            support = self._model_supports_thinking()
            api_kwargs["max_tokens"] = min(MAX_TOKENS_THINKING, model_cap) if model_cap else MAX_TOKENS_THINKING
            if support == "adaptive":
                api_kwargs["thinking"] = {"type": "adaptive"}
                if self.thinking_mode not in ("off", "adaptive"):
                    api_kwargs["output_config"] = {"effort": self.thinking_mode}
            elif support == "manual":
                api_kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
        else:
            api_kwargs["max_tokens"] = min(MAX_TOKENS, model_cap) if model_cap else MAX_TOKENS
            api_kwargs["temperature"] = self.temperature

        for attempt in range(max_retries):
            try:
                with self.client.beta.messages.stream(
                        betas=["web-search-2025-03-05", "code-execution-2025-08-25", "files-api-2025-04-14"],
                        **api_kwargs) as stream:
                    in_thinking = False
                    for event in stream:
                        if self.stop_requested:
                            break
                        if event.type == "content_block_start":
                            block = event.content_block
                            if hasattr(block, "type") and block.type == "thinking":
                                in_thinking = True
                                had_thinking = True
                                self.queue.put({"type": "thinking_start"})
                            elif hasattr(block, "type") and block.type == "text":
                                if had_thinking and in_thinking:
                                    self.queue.put({"type": "thinking_end"})
                                    in_thinking = False
                                if not label_emitted:
                                    self.queue.put({"type": "label"})
                                    label_emitted = True
                            elif hasattr(block, "type") and block.type == "server_tool_use":
                                tool_name = getattr(block, "name", "")
                                if tool_name == "web_search":
                                    self.queue.put({"type": "tool_info", "content": "Searching the web...\n"})
                                elif tool_name == "code_execution":
                                    self.queue.put({"type": "tool_info", "content": "Running code execution...\n"})
                            elif hasattr(block, "type") and block.type in (
                                    "code_execution_tool_result", "bash_code_execution_tool_result",
                                    "web_search_tool_result"):
                                pass  # Results extracted from final_message post-stream
                        elif event.type == "content_block_delta":
                            delta = event.delta
                            if hasattr(delta, "type") and delta.type == "thinking_delta":
                                self.queue.put({"type": "thinking_delta", "content": delta.thinking})
                            elif hasattr(delta, "type") and delta.type == "text_delta":
                                full_text += delta.text
                                self.queue.put({"type": "text_delta", "content": delta.text})
                        elif event.type == "content_block_stop":
                            if in_thinking:
                                self.queue.put({"type": "thinking_end"})
                                in_thinking = False
                    if self.stop_requested:
                        # Stream interrupted by user — synthesize a stop result
                        return "end_turn", [{"type": "text", "text": full_text}], full_text, had_thinking, label_emitted
                    try:
                        final_message = stream.get_final_message()
                    except Exception:
                        # Stream may be incomplete — synthesize a stop result
                        return "end_turn", [{"type": "text", "text": full_text}], full_text, had_thinking, label_emitted
                # Extract code execution images from final message
                # (file IDs are only available after streaming completes)
                for block in final_message.content:
                    if getattr(block, "type", None) in ("code_execution_tool_result",
                                                          "bash_code_execution_tool_result"):
                        content = getattr(block, "content", None)
                        items = content if isinstance(content, list) else [content] if content else []
                        for item in items:
                            itype = getattr(item, "type", None)
                            if itype in ("code_execution_result", "bash_code_execution_result"):
                                stdout = getattr(item, "stdout", "")
                                if stdout:
                                    self.queue.put({"type": "tool_info", "content": stdout + "\n"})
                                for sub in getattr(item, "content", []) or []:
                                    sub_type = getattr(sub, "type", None) or ""
                                    fid = getattr(sub, "file_id", "")
                                    if fid and ("output" in sub_type or sub_type == "file"):
                                        self.queue.put({"type": "ci_image", "url": "", "file_id": fid})
                            elif itype and ("output" in itype or itype == "file"):
                                fid = getattr(item, "file_id", "")
                                if fid:
                                    self.queue.put({"type": "ci_image", "url": "", "file_id": fid})
                break  # success
            except anthropic.RateLimitError as e:
                if attempt < max_retries - 1:
                    wait = min(2 ** attempt * 5, 60)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Rate limited — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                    full_text = ""
                else:
                    raise
            except anthropic.APIStatusError as e:
                if e.status_code == 529 and attempt < max_retries - 1:
                    wait = min(2 ** attempt * 10, 90)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"API overloaded — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                    full_text = ""
                else:
                    raise

        return final_message.stop_reason, final_message.content, full_text, had_thinking, label_emitted

    def _stream_responses_call(self, messages, max_retries, label_emitted):
        """Execute one OpenAI Responses API call with streaming and retry logic.
        Returns (stop_reason, content_blocks, full_text, had_thinking, label_emitted)."""
        system_prompt = self._build_system_prompt()
        tools = self._get_tools()
        responses_tools = self._tools_to_responses(tools) if tools else []
        responses_tools.append({"type": "web_search_preview"})
        responses_tools.append({"type": "code_interpreter", "container": {"type": "auto"}})
        responses_input = self._messages_to_responses(messages)
        is_reasoning = self._is_openai_reasoning_model()
        has_none = self._has_reasoning_none()

        api_kwargs = {
            "model": self.model,
            "input": responses_input,
            "instructions": system_prompt,
            "tools": responses_tools,
            "store": False,
            "include": ["code_interpreter_call.outputs"],
        }
        if has_none:
            # GPT-5.1+: always send reasoning param, even with effort="none"
            api_kwargs["reasoning"] = {"effort": self.thinking_effort, "summary": "auto"}
            if self.thinking_effort == "none":
                # gpt-5.4+ supports user temperature at effort=none; older models fixed at 1.0
                if self._gpt5_supports_temp_at_none():
                    api_kwargs["temperature"] = self.temperature
                else:
                    api_kwargs["temperature"] = 1.0
        elif self._is_gpt5_family():
            # GPT-5.0: always reasoning, temp fixed at 1.0
            if self.thinking_enabled:
                api_kwargs["reasoning"] = {"effort": self.thinking_effort, "summary": "auto"}
            api_kwargs["temperature"] = 1.0
        elif is_reasoning and self.thinking_enabled:
            api_kwargs["reasoning"] = {"effort": self.thinking_effort, "summary": "auto"}
        elif not is_reasoning:
            # gpt-5.x-chat Instant models don't support temperature
            if not self._is_gpt5_chat_model():
                api_kwargs["temperature"] = self.temperature
        # Verbosity for all gpt-5 models (including -chat Instant variants)
        if self._has_openai_verbosity():
            api_kwargs["text"] = {"verbosity": self.text_verbosity}

        FIRST_CONTENT_TIMEOUT = 180
        WAITING_MSG_INTERVAL = 15

        self._oai_first_content = threading.Event()
        self._oai_stream_start = time.time()
        self._oai_first_content_timeout = FIRST_CONTENT_TIMEOUT

        # Background thread posts elapsed-time messages every 15s until content arrives
        def _waiting_ticker():
            while not self._oai_first_content.wait(timeout=WAITING_MSG_INTERVAL):
                elapsed = int(time.time() - self._oai_stream_start)
                self.queue.put({
                    "type": "tool_info",
                    "content": f"Waiting for model response... ({elapsed}s elapsed)\n",
                })
        ticker = threading.Thread(target=_waiting_ticker, daemon=True)
        ticker.start()

        for attempt in range(max_retries):
            try:
                full_text, stop_reason, content_blocks, had_thinking, label_emitted = \
                    self._stream_responses(api_kwargs, label_emitted)
                break  # success
            except openai.BadRequestError as e:
                # Some models reject temperature — retry without it
                if "temperature" in str(e) and "temperature" in api_kwargs:
                    del api_kwargs["temperature"]
                    self.queue.put({
                        "type": "tool_info",
                        "content": "Model does not support temperature — retrying without it...\n",
                    })
                    full_text, stop_reason, content_blocks, had_thinking, label_emitted = \
                        self._stream_responses(api_kwargs, label_emitted)
                else:
                    raise
                break  # success
            except openai.APITimeoutError:
                # Reset timer for next attempt
                self._oai_first_content = threading.Event()
                self._oai_stream_start = time.time()
                ticker = threading.Thread(target=_waiting_ticker, daemon=True)
                ticker.start()
                if attempt < max_retries - 1:
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Stream timeout (no content from model within 180s) — retrying (attempt {attempt + 1}/{max_retries})...\n",
                    })
                else:
                    raise
            except openai.RateLimitError:
                if attempt < max_retries - 1:
                    wait = min(2 ** attempt * 5, 60)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Rate limited — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                else:
                    raise
            except openai.APIError as e:
                if attempt < max_retries - 1 and getattr(e, 'status_code', 0) >= 500:
                    wait = min(2 ** attempt * 10, 90)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"API error — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                else:
                    raise

        # Stop the ticker thread
        self._oai_first_content.set()
        return stop_reason, content_blocks, full_text, had_thinking, label_emitted

    def stream_worker(self, messages):
        try:
            # Sync temperature from spinbox
            try:
                self.temperature = max(0.0, min(1.0, self._temp_var.get()))
            except (tk.TclError, ValueError):
                pass

            label_emitted = False
            if not self.thinking_enabled:
                self.queue.put({"type": "label"})
                label_emitted = True

            call_num = 0
            while True:
                # Check stop request between API calls
                if self.stop_requested:
                    self.queue.put({"type": "tool_info", "content": "Agent stopped by user.\n"})
                    break

                call_num += 1
                if call_num > 1:
                    self.queue.put({"type": "ensure_newline"})
                payload_text = self._payload_for_display(messages)
                self.queue.put({"type": "call_counter", "content": call_num})
                self.queue.put({"type": "debug", "content": payload_text})

                max_retries = 10

                # Dispatch to provider-specific streaming
                if self.provider == "OpenAI":
                    stop_reason, content_blocks, full_text, had_thinking, label_emitted = \
                        self._stream_responses_call(messages, max_retries, label_emitted)
                elif self.provider == "Gemini":
                    stop_reason, content_blocks, full_text, had_thinking, label_emitted = \
                        self._stream_gemini_call(messages, max_retries, label_emitted)
                else:
                    stop_reason, content_blocks, full_text, had_thinking, label_emitted = \
                        self._stream_anthropic_call(messages, max_retries, label_emitted)

                # Post-process LaTeX in the just-completed text segment
                if full_text:
                    self.queue.put({"type": "post_process_latex"})

                if self.stop_requested:
                    self.queue.put({"type": "tool_info", "content": "Agent stopped by user.\n"})
                    break

                if stop_reason == "tool_use":
                    messages.append({"role": "assistant", "content": content_blocks})

                    # Wrap dict-based blocks (OpenAI/Gemini) in _ToolBlock for uniform attribute access
                    if self.provider in ("OpenAI", "Gemini"):
                        tool_blocks = [
                            _ToolBlock(b["name"], b["id"], b["input"])
                            for b in content_blocks if isinstance(b, dict) and b.get("type") == "tool_use"
                        ]
                    else:
                        tool_blocks = [b for b in content_blocks if b.type == "tool_use"]

                    # Log all tool calls up front
                    for block in tool_blocks:
                        tool_call_detail = json.dumps(
                            {"tool": block.name, "id": block.id, "input": block.input},
                            indent=2,
                        )
                        self.queue.put({"type": "tool_call_debug", "content": tool_call_detail})

                    # Partition into parallel-safe vs sequential, preserving original index
                    parallel_items = []   # [(index, block), ...]
                    sequential_items = [] # [(index, block), ...]
                    for idx, block in enumerate(tool_blocks):
                        if block.name in PARALLEL_SAFE_TOOLS:
                            parallel_items.append((idx, block))
                        else:
                            sequential_items.append((idx, block))

                    # Pre-allocate results list to preserve original order
                    tool_results_ordered = [None] * len(tool_blocks)

                    # Execute parallel-safe tools concurrently
                    if parallel_items:
                        if len(parallel_items) > 1:
                            self.queue.put({"type": "tool_info", "content": f"Running {len(parallel_items)} tools in parallel...\n"})
                        with concurrent.futures.ThreadPoolExecutor(max_workers=len(parallel_items)) as executor:
                            future_map = {}
                            for idx, block in parallel_items:
                                future = executor.submit(self._execute_tool, block)
                                future_map[future] = (idx, block)
                            for future in concurrent.futures.as_completed(future_map):
                                idx, block = future_map[future]
                                result = future.result()
                                tool_results_ordered[idx] = {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result,
                                }

                    # Execute sequential tools one at a time, in order
                    had_user_prompt = False
                    for idx, block in sequential_items:
                        result = self._execute_tool(block)
                        tool_results_ordered[idx] = {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                        if block.name == "user_prompt":
                            had_user_prompt = True

                    # After user_prompt, reset label so next response gets a fresh "Agent:" heading
                    if had_user_prompt:
                        label_emitted = False

                    messages.append({"role": "user", "content": tool_results_ordered})
                else:
                    # Normal end_turn — check if instruction expects interactivity
                    # If the instruction mentions user_prompt, the model likely forgot
                    # to call it. Auto-inject a user_prompt to keep the loop alive.
                    if "user_prompt" in self.agent_instruction:
                        self.queue.put({"type": "tool_info",
                                        "content": "Auto-prompting (agent ended turn without user_prompt)...\n"})
                        auto_response = self.do_user_prompt(
                            "The agent ended its turn. What would you like to do next? (Leave blank to stop)")
                        if not auto_response.strip():
                            break
                        messages.append({"role": "assistant", "content": full_text})
                        messages.append({"role": "user", "content": [
                            {"type": "text", "text": auto_response},
                        ]})
                        label_emitted = False
                        continue
                    break

            if full_text:
                messages.append({"role": "assistant", "content": full_text})
            self.queue.put({"type": "complete"})
            if self._headless:
                self.root.after(500, self._on_close)

        except Exception as e:
            self.queue.put({"type": "error", "content": str(e)})

    def _post_process_latex(self):
        """Replace LaTeX notation with Unicode in assistant-tagged text only."""
        try:
            # Get all ranges tagged as "assistant" and process each one
            ranges = self.chat_display.tag_ranges("assistant")
            # Process in reverse order so replacements don't shift later positions
            pairs = [(str(ranges[i]), str(ranges[i + 1])) for i in range(0, len(ranges), 2)]
            for start, end in reversed(pairs):
                raw = self.chat_display.get(start, end)
                if not raw:
                    continue
                converted = self._latex_to_unicode(raw)
                if converted != raw:
                    self.chat_display.delete(start, end)
                    self.chat_display.insert(start, converted, "assistant")
        except Exception:
            pass  # Don't break the UI if conversion fails

    def _ensure_newline(self):
        """Ensure the chat display ends with a newline so the next insert starts on a fresh line."""
        end_pos = self.chat_display.index("end-1c")
        if end_pos != "1.0":
            last_char = self.chat_display.get("end-2c", "end-1c")
            if last_char != "\n":
                self.chat_display.insert(tk.END, "\n")

    @staticmethod
    def _latex_to_unicode(text):
        """Convert LaTeX math notation to Unicode equivalents and strip delimiters."""
        # Strip display math delimiters first (longer patterns before shorter)
        text = text.replace("$$", "")
        text = text.replace("\\[", "")
        text = text.replace("\\]", "")
        # Strip inline math delimiters
        text = text.replace("\\(", "")
        text = text.replace("\\)", "")
        # Strip single $ delimiters (but not $$ which is already removed)
        # Use regex to avoid stripping $ in non-math contexts like currency
        text = re.sub(r'(?<!\$)\$(?!\$)', '', text)

        # Fractions: \frac{a}{b} → a/b
        text = re.sub(r'\\frac\{([^}]*)\}\{([^}]*)\}', r'\1/\2', text)

        # Square root: \sqrt{x} → √x, \sqrt → √
        text = re.sub(r'\\sqrt\{([^}]*)\}', r'√\1', text)
        text = text.replace("\\sqrt", "√")

        # Superscripts: ^{...} and ^x
        _sup_map = str.maketrans("0123456789+-=()nixy", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱˣʸ")
        def _sup_repl(m):
            return m.group(1).translate(_sup_map)
        # Only convert braced superscripts ^{...} to avoid false positives in code/filenames
        text = re.sub(r'\^\{([^}]*)\}', _sup_repl, text)

        # Subscripts: only braced _{...} to avoid false positives (e.g. sinc_plot.png)
        _sub_map = str.maketrans("0123456789+-=()aeiourxhklmnpst", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑᵢₒᵤᵣₓₕₖₗₘₙₚₛₜ")
        def _sub_repl(m):
            return m.group(1).translate(_sub_map)
        text = re.sub(r'_\{([^}]*)\}', _sub_repl, text)

        # Greek letters (common ones)
        _greek = {
            "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ",
            "\\epsilon": "ε", "\\zeta": "ζ", "\\eta": "η", "\\theta": "θ",
            "\\iota": "ι", "\\kappa": "κ", "\\lambda": "λ", "\\mu": "μ",
            "\\nu": "ν", "\\xi": "ξ", "\\pi": "π", "\\rho": "ρ",
            "\\sigma": "σ", "\\tau": "τ", "\\upsilon": "υ", "\\phi": "φ",
            "\\chi": "χ", "\\psi": "ψ", "\\omega": "ω",
            "\\Alpha": "Α", "\\Beta": "Β", "\\Gamma": "Γ", "\\Delta": "Δ",
            "\\Theta": "Θ", "\\Lambda": "Λ", "\\Pi": "Π", "\\Sigma": "Σ",
            "\\Phi": "Φ", "\\Psi": "Ψ", "\\Omega": "Ω",
        }
        # Operators, relations, arrows, sets, misc
        _symbols = {
            "\\times": "×", "\\div": "÷", "\\pm": "±", "\\mp": "∓",
            "\\cdot": "·", "\\cdots": "⋯", "\\ldots": "…",
            "\\le": "≤", "\\leq": "≤", "\\ge": "≥", "\\geq": "≥",
            "\\ne": "≠", "\\neq": "≠", "\\approx": "≈", "\\equiv": "≡",
            "\\infty": "∞", "\\propto": "∝",
            "\\sum": "Σ", "\\prod": "Π", "\\int": "∫",
            "\\to": "→", "\\rightarrow": "→", "\\leftarrow": "←",
            "\\Rightarrow": "⇒", "\\Leftarrow": "⇐",
            "\\leftrightarrow": "↔", "\\Leftrightarrow": "⇔",
            "\\in": "∈", "\\notin": "∉", "\\subset": "⊂", "\\subseteq": "⊆",
            "\\supset": "⊃", "\\supseteq": "⊇",
            "\\cup": "∪", "\\cap": "∩", "\\emptyset": "∅",
            "\\forall": "∀", "\\exists": "∃", "\\neg": "¬",
            "\\partial": "∂", "\\nabla": "∇", "\\degree": "°",
            "\\circ": "°", "\\prime": "′",
            # Functions (just strip the backslash)
            "\\sin": "sin", "\\cos": "cos", "\\tan": "tan",
            "\\sec": "sec", "\\csc": "csc", "\\cot": "cot",
            "\\arcsin": "arcsin", "\\arccos": "arccos", "\\arctan": "arctan",
            "\\sinh": "sinh", "\\cosh": "cosh", "\\tanh": "tanh",
            "\\log": "log", "\\ln": "ln", "\\exp": "exp",
            "\\lim": "lim", "\\max": "max", "\\min": "min",
            "\\det": "det", "\\dim": "dim",
        }
        # Apply all replacements (longer keys first to avoid partial matches)
        all_replacements = {**_greek, **_symbols}
        for latex, uni in sorted(all_replacements.items(), key=lambda x: -len(x[0])):
            text = text.replace(latex, uni)

        # Clean up remaining LaTeX formatting commands
        text = re.sub(r'\\(?:text|mathrm|mathbf|mathit|mathbb|mathcal|operatorname)\{([^}]*)\}', r'\1', text)
        text = re.sub(r'\\(?:left|right|big|Big|bigg|Bigg)', '', text)
        text = text.replace("\\,", " ")
        text = text.replace("\\;", " ")
        text = text.replace("\\!", "")
        text = text.replace("\\quad", "  ")
        text = text.replace("\\qquad", "    ")
        text = text.replace("\\\\", "\n")
        # Strip remaining \command patterns that we don't recognize (just remove backslash)
        text = re.sub(r'\\([a-zA-Z]+)', r'\1', text)
        # Clean up braces used for grouping
        text = re.sub(r'\{([^}]*)\}', r'\1', text)

        return text

    def check_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg["type"] == "debug" and not self.debug_enabled.get():
                    pass
                elif msg["type"] == "call_counter" and not self.show_activity.get() and not self.debug_enabled.get() and not self.tool_calls_enabled.get():
                    pass
                elif msg["type"] == "call_counter":
                    tag = "call_counter" if self.debug_enabled.get() else "call_counter_subtle"
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, f"  Call #{msg['content']}  ", tag)
                    self.chat_display.insert(tk.END, "\n", "debug")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "debug":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, "--- PAYLOAD SENT TO API ---\n", "debug_label")
                    self.chat_display.insert(tk.END, msg["content"] + "\n", "debug")
                    self.chat_display.insert(tk.END, "--- END PAYLOAD ---\n\n", "debug_label")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "tool_call_debug" and not self.tool_calls_enabled.get():
                    pass
                elif msg["type"] == "tool_call_debug":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, "--- TOOL CALL ---\n", "tool_debug_label")
                    self.chat_display.insert(tk.END, msg["content"] + "\n", "tool_debug")
                    self.chat_display.insert(tk.END, "--- END TOOL CALL ---\n", "tool_debug_label")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "thinking_start":
                    self._current_thinking_text = ""
                    if self.show_thinking.get():
                        self.chat_display.config(state="normal")
                        self._ensure_newline()
                        self.chat_display.insert(tk.END, "Thinking:\n", "thinking_label")
                        self.chat_display.see(tk.END)
                        self.chat_display.config(state="disabled")
                elif msg["type"] == "thinking_delta":
                    self._current_thinking_text += msg["content"]
                    if self.show_thinking.get():
                        self.chat_display.config(state="normal")
                        self.chat_display.insert(tk.END, msg["content"], "thinking")
                        self.chat_display.see(tk.END)
                        self.chat_display.config(state="disabled")
                elif msg["type"] == "thinking_end":
                    if self.show_thinking.get():
                        self.chat_display.config(state="normal")
                        self.chat_display.insert(tk.END, "\n\n", "thinking")
                        self.chat_display.see(tk.END)
                        self.chat_display.config(state="disabled")
                elif msg["type"] == "label":
                    self._current_response_text = ""
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, "Agent:\n", "assistant_label")
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "text_delta":
                    self._current_response_text += msg["content"]
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, msg["content"], "assistant")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "user_prompt_echo":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, "\nYou:\n", "user_label")
                    self.chat_display.insert(tk.END, msg["content"] + "\n\n", "user")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "ci_code" and not self.show_activity.get():
                    pass
                elif msg["type"] == "ci_code":
                    # Code interpreter code — display as a single readable block
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, msg["content"] + "\n", "tool_info")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "ci_image":
                    # Code interpreter image — decode/download, display inline, and save
                    try:
                        url = msg.get("url", "")
                        file_id = msg.get("file_id", "")
                        img_data = None
                        if url and url.startswith("data:"):
                            # data URL: data:image/png;base64,<data>
                            import base64
                            parts = url.split(",", 1)
                            if len(parts) == 2:
                                img_data = base64.b64decode(parts[1])
                        elif url:
                            import urllib.request
                            with urllib.request.urlopen(url, timeout=30) as resp:
                                img_data = resp.read()
                        elif file_id and self.provider == "Anthropic" and hasattr(self, 'client') and self.client:
                            resp = self.client.beta.files.download(file_id)
                            img_data = resp.read()
                        elif file_id and hasattr(self, 'openai_client') and self.openai_client:
                            content = self.openai_client.files.content(file_id)
                            img_data = content.read()
                        if img_data:
                            # Save to saved_chats dir
                            os.makedirs("saved_chats", exist_ok=True)
                            ts = time.strftime("%Y%m%d_%H%M%S")
                            img_path = os.path.join("saved_chats", f"ci_output_{ts}.png")
                            with open(img_path, "wb") as f:
                                f.write(img_data)
                            # Display inline in chat
                            pil_img = Image.open(io.BytesIO(img_data))
                            # Scale to fit chat display width (max ~600px)
                            max_w = 600
                            if pil_img.width > max_w:
                                ratio = max_w / pil_img.width
                                pil_img = pil_img.resize(
                                    (max_w, int(pil_img.height * ratio)),
                                    Image.LANCZOS,
                                )
                            from PIL import ImageTk
                            tk_img = ImageTk.PhotoImage(pil_img)
                            # Keep reference to prevent garbage collection
                            if not hasattr(self, '_ci_images'):
                                self._ci_images = []
                            self._ci_images.append(tk_img)
                            self.chat_display.config(state="normal")
                            self._ensure_newline()
                            self.chat_display.image_create(tk.END, image=tk_img)
                            self.chat_display.insert(tk.END, f"\n[Saved: {img_path}]\n", "tool_info")
                            self.chat_display.see(tk.END)
                            self.chat_display.config(state="disabled")
                    except Exception as e:
                        self.chat_display.config(state="normal")
                        self._ensure_newline()
                        self.chat_display.insert(tk.END, f"[Code interpreter image error: {e}]\n", "error")
                        self.chat_display.see(tk.END)
                        self.chat_display.config(state="disabled")
                elif msg["type"] == "tool_info" and not self.show_activity.get():
                    pass
                elif msg["type"] == "tool_info":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, msg["content"], "tool_info")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "warning":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, msg["content"], "warning")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "ensure_newline":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "post_process_latex":
                    self.chat_display.config(state="normal")
                    self._post_process_latex()
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "complete":
                    # Post-process: convert LaTeX to Unicode in the last response
                    self.chat_display.config(state="normal")
                    self._post_process_latex()
                    self.chat_display.insert(tk.END, "\n\n")
                    self.chat_display.config(state="disabled")
                    self.streaming = False
                    self._start_button.config(state="normal")
                    self._stop_button.config(state="disabled")
                    self.instruction_button.config(state="normal")
                elif msg["type"] == "error":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(
                        tk.END, f"Error: {msg['content']}\n\n", "error"
                    )
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                    self.streaming = False
                    self._start_button.config(state="normal")
                    self._stop_button.config(state="disabled")
                    self.instruction_button.config(state="normal")
        except queue.Empty:
            pass
        except Exception:
            pass
        self.root.after(50, self.check_queue)

    # ── Window Close ────────────────────────────────────────────────────

    def _on_close(self):
        if getattr(self, '_closing', False):
            return
        self._closing = True
        self.stop_requested = True
        if self.streaming:
            self.root.after(200, self._finish_close)
            return
        self._finish_close()

    def _finish_close(self):
        if self.streaming:
            self.root.after(200, self._finish_close)
            return
        self._save_last_state()
        self._auto_save_on_close()
        self._cleanup_browser()
        self._release_instance_lock()
        self.root.destroy()


if __name__ == "__main__":
    import argparse
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    parser = argparse.ArgumentParser(description="My Agent — autonomous task runner")
    parser.add_argument("-l", "--load", metavar="NAME",
                        help="Load an instruction by name and auto-start the agent")
    parser.add_argument("--headless", action="store_true",
                        help="Run without main window (dialogs still shown when needed)")
    args = parser.parse_args()
    root = tk.Tk()
    app = App(root, launch_instruction=args.load, headless=args.headless)
    root.mainloop()
