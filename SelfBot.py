import sys
IS_WINDOWS = sys.platform == "win32"

import ctypes
if IS_WINDOWS:
    import ctypes.wintypes
    # Fix DPI scaling for desktop automation tools — must run before any window creation.
    # Without this, Windows display scaling (125%, 150%, etc.) causes screenshot pixel
    # coordinates and mouse click coordinates to use different scales, so clicks miss.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk
from tkinter import messagebox, filedialog, ttk
import anthropic
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
import time
import signal
import concurrent.futures

# --- Optional MyAgent tool subsystems (reused as mixins) --------------------
# SelfBot borrows MyAgent's native MCP / Gmail / Proton(IMAP) / Outlook mixins
# verbatim. They share state with the host only through self.root / self.queue /
# self._disabled_confirm_patterns, so SelfBot's App can host them directly. All
# optional: a missing myagent package (or missing provider libs) degrades to
# disabled checkboxes with the feature simply absent. These tools are
# Anthropic-only in SelfBot (SelfBot has no other provider).
try:
    from myagent.constants import (
        GOOGLE_TOOLS, PROTON_TOOLS, OUTLOOK_TOOLS, MCP_TOOLS,
        GMAIL_CONFIRM_TOOLS, PROTON_CONFIRM_TOOLS, OUTLOOK_CONFIRM_TOOLS,
        ANTHROPIC_PRICING, APICOST_LOG_MAX_BYTES,
        _HAS_MCP, _HAS_GOOGLE, _HAS_PROTONMAIL, _HAS_OUTLOOK,
    )
    from myagent.helpers import rotate_log_if_needed
    from myagent.mcp_mixin import MCPMixin
    from myagent.gmail_mixin import GmailMixin
    from myagent.protonmail_mixin import ProtonMailMixin
    from myagent.outlook_mixin import OutlookMixin
    _HAS_MYAGENT_TOOLS = True
except Exception:
    _HAS_MYAGENT_TOOLS = False
    _HAS_MCP = _HAS_GOOGLE = _HAS_PROTONMAIL = _HAS_OUTLOOK = False
    GOOGLE_TOOLS = PROTON_TOOLS = OUTLOOK_TOOLS = MCP_TOOLS = []
    GMAIL_CONFIRM_TOOLS = PROTON_CONFIRM_TOOLS = OUTLOOK_CONFIRM_TOOLS = []
    ANTHROPIC_PRICING = {}
    APICOST_LOG_MAX_BYTES = 100_000

    def rotate_log_if_needed(log_path, max_bytes):
        return False  # no myagent package -> no rotation; the log just grows

    class MCPMixin:
        pass

    class GmailMixin:
        pass

    class ProtonMailMixin:
        pass

    class OutlookMixin:
        pass

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
    from PIL import Image
    # Desktop automation safety settings
    pyautogui.FAILSAFE = True   # move mouse to (0,0) to abort
    pyautogui.PAUSE = 0.3       # small delay between actions
except Exception:
    _HAS_DESKTOP = False
if IS_WINDOWS:
    try:
        import pygetwindow as gw
    except Exception:
        pass
    try:
        from PIL import ImageGrab
    except Exception:
        pass

_SUBPROCESS_NOWND = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}

# Tool definitions for the Anthropic API
TOOLS = [
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
]

# Meta tools — SelfBot's analog of MyAgent's META_TOOLS. MyAgent's manage_instructions/
# run_instruction are tied to its agent-instruction system, which SelfBot lacks; SelfBot's
# equivalents are its shared skills library and its saved system prompts. So SelfBot's Meta
# exposes manage_skills (shared skills.json, same as MyAgent) and manage_prompts (the
# system-prompt analog of manage_instructions). Gated by the Meta checkbox; provider-agnostic.
SELFBOT_META_TOOLS = [
    {
        "name": "manage_skills",
        "description": (
            "Manage the shared skills library on disk (skills.json, shared with MyAgent). "
            "Skills can be injected into the system prompt (enabled), retrieved on demand "
            "(on_demand), or inactive (disabled). Each skill may carry a short description "
            "(what it does + when to use it), listed in the system prompt for on_demand "
            "skills as the trigger signal. Actions: list, read, create, update, delete."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "read", "create", "update", "delete"],
                    "description": "The operation to perform",
                },
                "name": {"type": "string", "description": ("Skill name (required for all except list). "
                                                           "On create it MUST be Agent-Skills kebab-case — lowercase "
                                                           "letters/digits/hyphens, max 64 chars, e.g. 'westpac-login' "
                                                           "(non-conforming creates are rejected).")},
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
        "name": "manage_prompts",
        "description": (
            "Manage SelfBot's saved system prompts on disk (system_prompts.json). Unlike a "
            "plain prompt string, every SelfBot system prompt is a full ENVIRONMENT BUNDLE "
            "(the analog of a MyAgent instruction): the prompt text PLUS the model, thinking "
            "params, per-skill modes, tool-row toggles, the Safety confirm-bypass set, and "
            "the terminal/chatting-with names. On 'create' the current live model, thinking "
            "settings, skill modes and Safety set are inherited automatically; the tool "
            "toggles default OFF unless you pass them. 'read' returns the bundled environment "
            "too, and 'update' can change any bundled field. Loading a prompt (from the GUI) "
            "restores that whole environment. create/update/delete are saved to disk and do "
            "NOT change the running session; the 'apply' action loads a saved prompt into the "
            "LIVE session (environment + text) at once — the only action that mutates live "
            "state. The 'Default' prompt cannot be deleted. SelfBot is Anthropic-only, so "
            "there is no provider/conversational field. Actions: list, read, create, update, "
            "delete, apply."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "read", "create", "update", "delete", "apply"],
                    "description": "The operation to perform. 'apply' loads the named prompt into the LIVE running session (environment + text) immediately; create/update/delete only touch disk.",
                },
                "name": {"type": "string", "description": "System prompt name (required for all except list)"},
                "text": {
                    "type": "string",
                    "description": "Prompt text (required for create, optional for update)",
                },
                "model": {"type": "string", "description": "Anthropic model id to bundle (optional; create inherits the current model)"},
                "temperature": {"type": "number", "description": "Temperature 0.0-1.0 (optional; create inherits current)"},
                "thinking_enabled": {"type": "boolean", "description": "Enable extended thinking (optional; create inherits current)"},
                "thinking_effort": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "xhigh", "max"],
                    "description": "Thinking effort (optional; create inherits current). 'xhigh'/'max' need newer Opus/Sonnet/Fable tiers.",
                },
                "thinking_budget": {"type": "integer", "description": "Manual thinking token budget for Opus/Sonnet/Haiku 4.5 (optional; create inherits current)"},
                "thinking_mode": {
                    "type": "string",
                    "enum": ["off", "adaptive", "low", "medium", "high", "xhigh", "max"],
                    "description": "Adaptive thinking mode for newer models (optional; create inherits current). Fable/Mythos 5 are always-on — 'off' is invalid there, use 'adaptive'.",
                },
                "desktop": {"type": "boolean", "description": "Bundle Desktop tools ON (default false on create)"},
                "browser": {"type": "boolean", "description": "Bundle Browser tools ON (default false on create)"},
                "meta": {"type": "boolean", "description": "Bundle Meta tools ON (default false on create)"},
                "mcp": {"type": "boolean", "description": "Bundle MCP tools ON (default false on create)"},
                "google": {"type": "boolean", "description": "Bundle Gmail tools ON (default false on create)"},
                "proton": {"type": "boolean", "description": "Bundle Proton/IMAP mail tools ON (default false on create)"},
                "outlook": {"type": "boolean", "description": "Bundle Outlook tools ON (default false on create)"},
                "pause": {"type": "boolean", "description": "Bundle the pause_conversation (rest the self-chat) tool ON (default false on create)"},
                "skill_modes": {
                    "type": "object",
                    "description": "Map of skill name -> mode ('disabled'/'enabled'/'on_demand'). On create, defaults to the current skill modes; on update, only listed skills change.",
                    "additionalProperties": {"type": "string", "enum": ["disabled", "enabled", "on_demand"]},
                },
                "my_name": {"type": "string", "description": "Bundle the 'Terminal user' name (optional; omit to leave names unbundled)"},
                "my_friend": {"type": "string", "description": "Bundle the 'Chatting with' name (optional; omit to leave names unbundled)"},
            },
            "required": ["action"],
        },
    },
]

# Pause/rest tool — the duo's hang-up button that isn't a shutdown. Auto-chat
# re-injects every completed turn into the peer forever, so without this the only
# way a self-chat could genuinely END was killing a process (taskkill). Gated by
# the Pause checkbox (wired like MyAgent's Convo toggle: a per-environment
# behavioural affordance, persisted in app_state.json and the prompt bundle).
# The description is kept neutral — it states what the tool does, not when to
# prefer it — so duo runs measure the model's own choice of exit.
PAUSE_TOOLS = [
    {
        "name": "pause_conversation",
        "description": (
            "Let the conversation rest. Switches this instance's auto-chat OFF, so "
            "anything you write after this call appears in your own window but is "
            "NOT delivered to your conversation partner — the chat simply goes "
            "quiet. Both windows stay open; the human can read the chats or close "
            "them at leisure. The rest holds until new traffic arrives: a fresh "
            "message from the human or your partner (or the human clicking "
            "'Auto: ON') resumes the conversation automatically. No process is "
            "terminated. If no partner is connected, this just switches auto-chat "
            "off."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional short note on why the conversation is resting (shown to the human).",
                }
            },
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

# Command safety guardrails — two-tier system, platform-specific
# Tier 1: Hard-blocked patterns (rejected outright, never run)
# Tier 2: Confirmation-required patterns (user must approve via dialog)

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
# Prompt-cache breakpoint marker. 5-minute TTL (the default) is deliberate:
# writes cost 1.25x and match ANTHROPIC_PRICING's cache-write column exactly,
# so _get_pricing's four-bucket cost stays accurate. A "ttl": "1h" variant
# would need the extended-cache-ttl-2025-04-11 beta and 2x writes.
CACHE_CONTROL = {"type": "ephemeral"}
# Models with lower max output token limits than MAX_TOKENS. Empty since the
# 2026 retirements: the Claude 3 generation (the only 4K-cap models) is fully
# retired. Kept as a dict because stream_worker .get()s it per call.
MODEL_MAX_OUTPUT_TOKENS = {}
# Deprecated / soon-to-be-retired model id prefixes hidden from the picker.
# _fetch_available_models filters the live models.list() against these, so newer
# models appear automatically and only the retiring ones drop out. Opus 4.5 /
# Sonnet 4.5 stay — still active. The dated 4.0 ids are claude-opus-4-20250514 /
# claude-sonnet-4-20250514, matched by the "-4-20" prefix (a real "-4-20" minor
# is implausible — minors run 5, 6, 7, 8…).
DEPRECATED_MODEL_PREFIXES = (
    "claude-opus-4-1",       # Opus 4.1 — deprecated (retires 2026-08-05)
    "claude-opus-4-0",       # Opus 4.0 alias
    "claude-opus-4-20",      # Opus 4.0 dated id (claude-opus-4-20250514)
    "claude-sonnet-4-0",     # Sonnet 4.0 alias
    "claude-sonnet-4-20",    # Sonnet 4.0 dated id (claude-sonnet-4-20250514)
    "claude-3",              # every Claude 3.x — retired/deprecated
    "claude-2",              # Claude 2.x — retired
)
# Exact-match aliases for adaptive-thinking models; the version-parsed
# _is_adaptive_model backstops dated snapshots and future Opus/Sonnet 4.6+ minors.
ADAPTIVE_THINKING_MODELS = {"claude-fable-5", "claude-mythos-5",
                            "claude-opus-5",
                            "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                            "claude-sonnet-5", "claude-sonnet-4-6"}
# Claude 5 Mythos-class (Fable 5 / Mythos 5): thinking is ALWAYS ON — the API
# rejects thinking={"type": "disabled"} and budget_tokens with HTTP 400, and
# sampling params (temperature/top_p/top_k) are rejected unconditionally.
ALWAYS_ON_THINKING_PREFIXES = ("claude-fable-", "claude-mythos-")
# Budget-based ("manual") extended thinking — Opus/Sonnet 4.5 and Haiku 4.5.
# claude-3-5-sonnet is deliberately excluded: extended thinking arrived with
# 3.7 / 4, so a thinking block to a 3.5 model is HTTP 400.
MANUAL_THINKING_PREFIXES = ("claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-5")
EFFORT_LEVELS = ["low", "medium", "high", "xhigh", "max"]
BUDGET_PRESETS = {"1K": 1024, "4K": 4096, "8K": 8192, "16K": 16384, "32K": 32768}
# Static superset for the combobox placeholder; _anthropic_mode_values() builds
# the real per-model list (drops "Off" for always-on models, gates Xhigh/Max).
ADAPTIVE_MODE_VALUES = ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]
DEFAULT_GEOMETRY = "1050x930"
CASCADE_OFFSET = 60  # px a manually-opened 2nd instance cascades off instance 1 so they don't stack
MONO_FONT = "Consolas" if IS_WINDOWS else "Menlo"

DEFAULT_SYSTEM_PROMPT = (
    "You are a capable personal assistant for Roman with access to a rich set of tools. "
    "Use them proactively — never tell Roman to do something you can do yourself.\n\n"

    "CORE TOOLS (always available):\n"
    "• web_search — search the web for current information. Use this whenever a question "
    "involves recent events, weather, prices, news, facts you're unsure about, or anything "
    "that benefits from up-to-date data.\n"
    "• fetch_webpage — fetch and read a specific URL. Use after web_search to get full "
    "details from a result, or when Roman provides a link.\n"
    "• run_command — execute commands on Roman's machine (PowerShell on Windows, bash on macOS). Use for file "
    "operations, system info, installing software, running scripts, or any local task.\n"
    "• csv_search — search a delimited text file (CSV, TSV, TXT, etc.) for records by column "
    "heading and value. Auto-detects the delimiter or accepts an explicit one. Use whenever "
    "Roman asks to find, look up, or filter data in a CSV or text file. Supports searching a "
    "specific column or all columns, with contains/exact/starts_with matching.\n\n"

    "DESKTOP TOOLS (available when Desktop is enabled):\n"
    "• screenshot — capture the screen. Always take a screenshot FIRST to see what's on "
    "screen before clicking or typing.\n"
    "• mouse_click — click at specific coordinates from the screenshot.\n"
    "• type_text — type text at the current cursor position.\n"
    "• press_key — press keys or combos (e.g. 'ctrl+c'/'command+c', 'enter', 'alt+tab'/'command+tab').\n"
    "• mouse_scroll — scroll up or down.\n"
    "• open_application — launch apps by name (chrome, notepad, vscode, etc.) or path.\n"
    "• find_window — find and optionally activate windows by title.\n"
    "• clipboard_read — read the current text from the clipboard.\n"
    "• clipboard_write — write text to the clipboard.\n"
    "• wait_for_window — wait until a window with a given title appears (with timeout).\n"
    "• read_screen_text — OCR a screen region to extract text.\n"
    "• find_image_on_screen — find a reference image on screen and return its coordinates.\n"
    "• mouse_drag — drag the mouse from one point to another (drag-and-drop, sliders, etc.).\n\n"

    "BROWSER TOOLS (available when Browser is enabled):\n"
    "• browser_open — connect to the system browser (Edge or Chrome) with Roman's real profile (cookies, logins, extensions) "
    "and navigate to a URL. Call this first before other browser tools.\n"
    "• browser_navigate — go to a new URL in the connected browser.\n"
    "• browser_click — click an element by CSS selector or visible text.\n"
    "• browser_fill — fill a form field by CSS selector.\n"
    "• browser_get_text — read text content from the page or a specific element.\n"
    "• browser_run_js — execute JavaScript on the page.\n"
    "• browser_screenshot — take a screenshot of the browser page.\n"
    "• browser_close — disconnect from the browser (Edge stays open).\n"
    "• browser_wait_for — wait for an element (CSS selector) to appear on the page.\n"
    "• browser_select — select an option from a <select> dropdown by value or label.\n"
    "• browser_get_elements — get info (tag, text, attributes, visibility) about matching elements.\n\n"

    "GUIDELINES:\n"
    "• Be direct and helpful. Provide answers, don't suggest Roman look things up.\n"
    "• When multiple tools can achieve a goal, chain them together without asking.\n"
    "• For desktop automation: screenshot first, then act on what you see.\n"
    "• For browser tasks: use browser tools (not desktop tools) for precision.\n"
    "• Refer to Roman by name when it makes the conversation flow naturally.\n"
    "• If you genuinely don't know something and can't find it, say so honestly."
)

# The authored-content stores (system prompts + the skills library shared with
# MyAgent) live in <OneDrive>/MyAppShare when a OneDrive client is present — one
# copy follows the user across machines; OneDrive, not git, is the sync channel
# (see myagent/datapaths.py). Repo-root fallback on solo machines, and plain
# repo-root behaviour when the myagent package is absent. State files
# (app_state*.json etc.) stay per-machine at the repo root either way.
# Since 2026-08-07 the skills library is a per-skill file tree
# (skills/<Name>/SKILL.md, frontmatter name/description/mode over the markdown
# content — Agent-Skills-shaped) rather than one skills.json; a legacy
# skills.json migrates into the tree on first load. The stub fallbacks below
# mirror the same file format so a standalone SelfBot.py stays compatible.
try:
    from myagent.datapaths import (
        resolve_store as _resolve_store,
        resolve_costlog as _resolve_costlog,
        load_store as _load_store,
        save_store as _save_store,
        absorb_conflict_forks as _absorb_conflict_forks,
        resolve_skills_dir as _resolve_skills_dir,
        load_skills_tree as _load_skills_tree,
        save_skills_tree as _save_skills_tree,
        delete_skill_tree_entry as _delete_skill_tree_entry,
    )
except ImportError:
    def _resolve_store(name):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)

    def _resolve_costlog():
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "APICostLog.txt")

    def _load_store(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_store(path, data):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _absorb_conflict_forks(path, data):
        return False

    # Compact skills-tree fallbacks — same skills/<Name>/SKILL.md format as
    # myagent.datapaths (frontmatter name/description/mode + markdown body),
    # minus the OneDrive-specific fork healing.
    _SB_SKILL_MODES = ("disabled", "enabled", "on_demand")

    def _resolve_skills_dir():
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")

    def _sb_skill_dirname(name):
        return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .") or "_skill"

    def _sb_skill_serialize(name, entry):
        lines = ["---", "name: " + " ".join(name.split())]
        desc = " ".join((entry.get("description") or "").split())
        if desc:
            lines.append("description: " + desc)
        lines.append("mode: " + entry.get("mode", "disabled"))
        lines.append("---")
        body = entry.get("content", "")
        if body and not body.endswith("\n"):
            body += "\n"
        return "\n".join(lines) + "\n\n" + body

    def _sb_skill_parse(text):
        lines = text.split("\n")
        if not lines or lines[0].strip() != "---":
            return {}, text
        meta, key, closed, i = {}, None, False, 1
        while i < len(lines):
            raw = lines[i]
            if raw.strip() == "---":
                closed = True
                i += 1
                break
            m = re.match(r"([A-Za-z][A-Za-z0-9_-]*)[ \t]*:[ \t]?(.*)$", raw)
            if m:
                key = m.group(1).strip().lower()
                meta[key] = m.group(2).rstrip("\r").strip()
            elif key is not None and raw.strip():
                meta[key] = (meta[key] + " " + raw.strip()).strip()
            i += 1
        if not closed:
            return {}, text
        if i < len(lines) and not lines[i].strip():
            i += 1
        body = "\n".join(lines[i:])
        return meta, body[:-1] if body.endswith("\n") else body

    def _save_skills_tree(dirpath, skills):
        try:
            os.makedirs(dirpath, exist_ok=True)
        except OSError:
            return
        for name, entry in skills.items():
            if not isinstance(entry, dict):
                continue
            d = os.path.join(dirpath, _sb_skill_dirname(name))
            md = os.path.join(d, "SKILL.md")
            text = _sb_skill_serialize(name, entry)
            try:
                with open(md, encoding="utf-8-sig") as f:
                    if f.read() == text:
                        continue
            except OSError:
                pass
            try:
                os.makedirs(d, exist_ok=True)
                with open(md, "w", encoding="utf-8") as f:
                    f.write(text)
            except OSError:
                pass

    def _load_skills_tree(dirpath):
        skills = {}
        if os.path.isdir(dirpath):
            for sub in sorted(os.listdir(dirpath)):
                md = os.path.join(dirpath, sub, "SKILL.md")
                if not os.path.isfile(md):
                    continue
                try:
                    with open(md, encoding="utf-8-sig") as f:
                        meta, content = _sb_skill_parse(f.read())
                except OSError:
                    continue
                name = " ".join((meta.get("name") or "").split()) or sub
                mode = (meta.get("mode") or "").strip().lower()
                entry = {"content": content,
                         "mode": mode if mode in _SB_SKILL_MODES else "disabled"}
                desc = (meta.get("description") or "").strip()
                if desc:
                    entry["description"] = desc
                skills[name] = entry
        # One-shot: fold a legacy repo-root skills.json into the tree (tree
        # entries win their names), then park it as .migrated.bak.
        legacy = _resolve_store("skills.json")
        data = _load_store(legacy)
        if data:
            for name, entry in data.items():
                if isinstance(entry, dict) and name not in skills:
                    entry = dict(entry)
                    if "mode" not in entry:
                        entry["mode"] = "enabled" if entry.pop("enabled", False) else "disabled"
                    skills[name] = entry
            _save_skills_tree(dirpath, skills)
            try:
                os.replace(legacy, legacy + ".migrated.bak")
            except OSError:
                pass
        return skills

    def _delete_skill_tree_entry(dirpath, name):
        # Retry the OneDrive/Windows handle race: rmtree can remove the files
        # but fail the final rmdir while the sync client holds the folder,
        # which would otherwise leave an empty husk directory behind.
        import shutil
        target = os.path.join(dirpath, _sb_skill_dirname(name))
        for attempt in range(6):
            if not os.path.exists(target):
                return
            try:
                shutil.rmtree(target)
            except OSError:
                pass
            if not os.path.exists(target):
                return
            time.sleep(0.25 * (attempt + 1))

PROMPTS_FILE = _resolve_store("system_prompts.json")
# Same cost log MyAgent writes: APICostLog_<machine>.txt in the OneDrive share
# (per-machine files — appends never conflict-fork, every machine's spend syncs
# everywhere for the viewers to aggregate), repo-root APICostLog.txt fallback.
APICOST_LOG_FILE = _resolve_costlog()
CHATS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved_chats")
APP_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_state.json")
APP_STATE_FILE_2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_state_2.json")
SKILLS_DIR = _resolve_skills_dir()  # per-skill SKILL.md tree; a legacy skills.json migrates in on first load
STORES_SYNCED = os.path.dirname(PROMPTS_FILE) != os.path.dirname(os.path.abspath(__file__))
LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot.lock")
INJECT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot_inject.txt")
AUTO_MSG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot_auto_msg.json")



def _get_window_pid(hwnd):
    """Get the process ID that owns a given window handle."""
    if IS_WINDOWS:
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    return 0


class App(MCPMixin, GmailMixin, ProtonMailMixin, OutlookMixin):
    def __init__(self, root):
        self.root = root
        self.root.title("Claude SelfBot")
        self.root.geometry(DEFAULT_GEOMETRY)

        # Check for API key
        if not os.environ.get("ANTHROPIC_API_KEY"):
            messagebox.showerror(
                "API Key Missing",
                "Please set the ANTHROPIC_API_KEY environment variable.",
            )
            self.root.destroy()
            return

        # Initialize API client and state
        self.client = anthropic.Anthropic()
        self.messages = []
        self.queue = queue.Queue()
        self.streaming = False
        self._session_cost = 0.0  # cumulative Anthropic API cost for this process (logged on close)
        self.pending_images = []  # list of (base64_data, media_type, filename)
        self._screenshot_scale = 1.0  # ratio to convert image coords → screen coords
        self._screenshot_offset = (0, 0)  # display origin offset for per-display screenshots
        self._screenshot_dims = (0, 0)    # (width, height) of last screenshot sent to model
        self.debug_enabled = tk.BooleanVar(value=False)
        self.tool_calls_enabled = tk.BooleanVar(value=False)
        self.show_activity = tk.BooleanVar(value=False)
        self.show_thinking = tk.BooleanVar(value=False)
        self.save_thinking = tk.BooleanVar(value=False)
        self.desktop_enabled = tk.BooleanVar(value=False)
        self.browser_enabled = tk.BooleanVar(value=False)
        # MyAgent-style tool subsystems (Meta / MCP / Google / IMAP / Outlook).
        self.meta_enabled = tk.BooleanVar(value=False)
        self.mcp_enabled = tk.BooleanVar(value=False)
        self.google_enabled = tk.BooleanVar(value=False)
        self.proton_enabled = tk.BooleanVar(value=False)
        self.outlook_enabled = tk.BooleanVar(value=False)
        # SelfBot-native Pause/rest tool (see PAUSE_TOOLS) — lets the model end a
        # self-chat by going quiet instead of killing a process.
        self.pause_enabled = tk.BooleanVar(value=False)
        # Confirm-bypass set — command regex patterns (checked in _check_command_safety)
        # AND mail tool names (read by the mail mixins' confirm_action). Managed via the
        # Safety dialog; empty = every risky command / mail action confirms.
        self._disabled_confirm_patterns = set()
        self._ps_safety_dialog = None            # open Safety dialog (or None)
        self._last_ps_safety_geometry = None     # persisted Safety dialog geometry
        # Seed each subsystem's instance state (connection caches, etc.).
        if _HAS_MYAGENT_TOOLS:
            self._init_mcp_state()
            self._google_init_state()
            self._proton_init_state()
            self._outlook_init_state()
        self._playwright = None
        self._browser = None
        self._page = None
        self._edge_process = None
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        self.system_prompt_name = ""
        self.model = DEFAULT_MODEL
        self.temperature = 1.0
        self.thinking_enabled = False
        self.thinking_effort = "high"
        self.thinking_budget = 8192
        self.thinking_mode = "off"
        # Models that 400'd on temperature this session — a reactive backstop for
        # any rejecting model the version parser doesn't know yet (e.g. a future
        # Haiku tier). Populated by the stream_worker BadRequest handler.
        self._no_temperature = set()
        self.prompt_editor_window = None
        self.skills_editor_window = None
        self._skills_refresh_list = None            # set while the Skills Manager is open
        self._last_skills_dialog_geometry = None    # persisted Skills Manager geometry
        self.skills = self._load_skills()
        self.available_models = self._fetch_available_models()

        # Detect second instance
        if IS_WINDOWS:
            # Named mutex (OS auto-releases on crash/kill)
            _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._mutex = _k32.CreateMutexW(None, True, "SelfBotInstanceMutex")
            if ctypes.get_last_error() == 183:          # ERROR_ALREADY_EXISTS
                self._is_second_instance = True
                self._state_file = APP_STATE_FILE_2
            else:
                self._is_second_instance = False
                self._state_file = APP_STATE_FILE
                try:
                    with open(LOCK_FILE, "w") as f:
                        f.write(str(os.getpid()))
                except OSError:
                    pass
        else:
            # Lock-file-based detection for macOS
            self._is_second_instance = False
            try:
                if os.path.exists(LOCK_FILE):
                    with open(LOCK_FILE) as f:
                        old_pid = int(f.read().strip())
                    try:
                        os.kill(old_pid, 0)
                        # PID alive — verify it's a SelfBot process
                        result = subprocess.run(
                            ["ps", "-p", str(old_pid), "-o", "command="],
                            capture_output=True, text=True, timeout=5,
                        )
                        if "SelfBot.py" in result.stdout:
                            self._is_second_instance = True
                    except (OSError, subprocess.TimeoutExpired):
                        pass  # stale lock
            except (ValueError, OSError):
                pass
            if self._is_second_instance:
                self._state_file = APP_STATE_FILE_2
            else:
                self._state_file = APP_STATE_FILE
                try:
                    with open(LOCK_FILE, "w") as f:
                        f.write(str(os.getpid()))
                except OSError:
                    pass
        self._response_count = 0
        self._first_message_text = ""
        self._current_response_text = ""
        self._current_thinking_text = ""
        self._duo_mode = "--no-geometry" in sys.argv

        self.setup_ui()
        self._load_last_state()
        self._my_pid = os.getpid()
        # Save state immediately so instance 2 always has fresh data
        try:
            self._save_last_state()
        except Exception:
            pass
        self.root.after(50, self.check_queue)
        self.root.after(5000, self._periodic_save)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        if not IS_WINDOWS:
            # macOS: a SIGTERM (peer duo-shutdown, kill/pkill) runs the same
            # graceful close as [X] — Windows gets this for free via WM_CLOSE.
            try:
                signal.signal(signal.SIGTERM, self._handle_sigterm)
            except ValueError:
                pass  # not on the main thread (never in practice)
        # Connect MCP servers after the UI is up so a slow stdio handshake never
        # blocks launch (no-op without mcp_servers.json / the mcp package).
        if _HAS_MYAGENT_TOOLS and _HAS_MCP:
            self.root.after(100, self._connect_mcp_servers)
        # Instance 2: start polling for injected chat content
        if self._is_second_instance:
            self.root.after(500, self._poll_inject_file)
            # Retry loading names if they came up empty (race with instance 1)
            self.root.after(2000, self._retry_load_names)
        # Poll for peer instance to enable/disable auto-chat and send delay
        self.root.after(2000, self._poll_for_peer)
        # Poll for auto-injected messages from the other instance
        self.root.after(500, self._poll_auto_msg)

    def setup_ui(self):
        # Grid weights for resizing
        self.root.grid_rowconfigure(0, weight=0, minsize=40)
        self.root.grid_rowconfigure(1, weight=0)
        self.root.grid_rowconfigure(2, weight=0)
        self.root.grid_rowconfigure(3, weight=1)
        self.root.grid_rowconfigure(4, weight=0)
        self.root.grid_rowconfigure(5, weight=0)
        self.root.grid_rowconfigure(6, weight=0)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=0)

        # Model selection toolbar (row 1)
        model_toolbar = tk.Frame(self.root)
        model_toolbar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 0))

        tk.Label(model_toolbar, text="Model", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        # Show display names in dropdown, map back to model IDs
        self._model_id_list = self.available_models
        display_names = [
            self._model_display_names.get(mid, mid) for mid in self._model_id_list
        ]
        current_display = self._model_display_names.get(self.model, self.model)
        self._model_var = tk.StringVar(value=current_display)
        self._model_combo = ttk.Combobox(
            model_toolbar, textvariable=self._model_var, state="readonly",
            font=("Arial", 9), width=28
        )
        self._model_combo["values"] = display_names
        self._model_combo.pack(side=tk.LEFT, padx=(0, 10))
        self._model_combo.bind("<<ComboboxSelected>>", self._on_model_selected)

        self._temp_label = tk.Label(model_toolbar, text="Temp", font=("Arial", 10))
        self._temp_label.pack(side=tk.LEFT, padx=(10, 5))
        self._temp_var = tk.DoubleVar(value=self.temperature)
        self._temp_spin = tk.Spinbox(
            model_toolbar, textvariable=self._temp_var,
            from_=0.0, to=1.0, increment=0.1,
            width=5, font=("Arial", 10), format="%.1f",
            command=self._on_temp_changed,
        )
        self._temp_spin.pack(side=tk.LEFT, padx=(0, 10))
        self._temp_spin.bind("<Return>", lambda e: self._on_temp_changed())
        self._temp_spin.bind("<FocusOut>", lambda e: self._on_temp_changed())

        self._thinking_var = tk.BooleanVar(value=False)
        self._thinking_check = tk.Checkbutton(
            model_toolbar, text="Thinking", variable=self._thinking_var,
            font=("Arial", 10), command=self._on_thinking_toggled,
        )
        self._thinking_check.pack(side=tk.LEFT, padx=(10, 2))

        self._thinking_strength_var = tk.StringVar(value="high")
        self._thinking_strength_combo = ttk.Combobox(
            model_toolbar, textvariable=self._thinking_strength_var, state="disabled",
            font=("Arial", 9), width=6,
        )
        self._thinking_strength_combo.pack(side=tk.LEFT, padx=(0, 10))
        self._thinking_strength_combo.bind("<<ComboboxSelected>>", lambda e: self._on_thinking_strength_changed())

        # Adaptive thinking mode combobox (hidden by default, shown for adaptive models)
        self._thinking_mode_var = tk.StringVar(value="Off")
        self._thinking_mode_label = tk.Label(model_toolbar, text="Thinking", font=("Arial", 10))
        self._thinking_mode_combo = ttk.Combobox(
            model_toolbar, textvariable=self._thinking_mode_var, state="readonly",
            font=("Arial", 9), width=8,
        )
        self._thinking_mode_combo["values"] = ADAPTIVE_MODE_VALUES
        self._thinking_mode_combo.bind("<<ComboboxSelected>>", lambda e: self._on_thinking_mode_changed())
        # Not packed yet — _on_model_selected() will show/hide as needed

        tk.Button(model_toolbar, text="NEW CHAT", command=self._new_chat, width=10).pack(side=tk.RIGHT, padx=(5, 0))
        tk.Button(model_toolbar, text="DELETE", command=self._delete_chat, width=8).pack(side=tk.RIGHT, padx=(10, 5))

        # Names toolbar (row 0)
        names_toolbar = tk.Frame(self.root)
        names_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 0))

        tk.Label(names_toolbar, text="Terminal user", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self.my_name_entry = tk.Entry(names_toolbar, font=("Arial", 10), width=14)
        self.my_name_entry.pack(side=tk.LEFT, padx=(0, 15))

        tk.Label(names_toolbar, text="Chatting with", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self.my_friend_entry = tk.Entry(names_toolbar, font=("Arial", 10), width=14)
        self.my_friend_entry.pack(side=tk.LEFT, padx=(0, 15))

        # Auto-chat starts disabled (solo mode); enabled when peer detected
        self._auto_chat = tk.BooleanVar(value=False)
        self._auto_chat_user_off = False  # set when user manually toggles off; cleared when peer leaves
        self._ever_had_peer = False  # set True when a peer is first detected; arms the duo-shutdown watchdog
        self._peer_gone_polls = 0  # consecutive peer-less polls once armed (watchdog debounce)
        self._pending_injection = False  # True when a response completed but wasn't injected (Auto was OFF)
        self._model_paused = False  # True after pause_conversation; lifted by new traffic or a manual Auto toggle
        self._send_delay = 0  # 0 when solo, delay_seconds*1000 when paired
        self._delay_seconds = 5  # default, overwritten by persisted value in _load_last_state
        if not self._is_second_instance:
            if IS_WINDOWS:
                self._auto_chat_btn = tk.Button(
                    names_toolbar, text="Auto: OFF", font=("Arial", 9),
                    width=10, command=self._toggle_auto_chat,
                    bg="#c62828", fg="white", pady=0, bd=1, highlightthickness=0,
                )
            else:
                # macOS: Aqua tk.Buttons ignore bg — the face stays white, so the
                # white fg made the green/red state unreadable. A Label honours
                # bg/fg on Aqua; style it as a button and bind the click —
                # .config/.pack/.winfo_ismapped all behave identically.
                self._auto_chat_btn = tk.Label(
                    names_toolbar, text="Auto: OFF", font=("Arial", 9),
                    width=10, bg="#c62828", fg="white", bd=1, relief="raised",
                    padx=4, cursor="pointinghand",
                )
                self._auto_chat_btn.bind("<Button-1>", lambda e: self._toggle_auto_chat())
            # Start hidden — shown by _poll_for_peer when paired
            # Delay selector (also hidden until peer detected)
            self._delay_label = tk.Label(names_toolbar, text="Delay(s)", font=("Arial", 10))
            self._delay_var = tk.IntVar(value=self._delay_seconds)
            self._delay_spin = tk.Spinbox(
                names_toolbar, textvariable=self._delay_var,
                from_=0, to=30, increment=1,
                width=3, font=("Arial", 10),
                command=self._on_delay_changed,
            )
            self._delay_spin.bind("<Return>", lambda e: self._on_delay_changed())
            self._delay_spin.bind("<FocusOut>", lambda e: self._on_delay_changed())
            # Start hidden — shown by _poll_for_peer when paired
            self._delay_label.pack_forget()
            self._delay_spin.pack_forget()

        # Chat management toolbar (row 2)
        chat_toolbar = tk.Frame(self.root)
        chat_toolbar.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 0))

        tk.Label(chat_toolbar, text="Save Chat as", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self.chat_name_entry = tk.Entry(chat_toolbar, font=("Arial", 10), width=20)
        self.chat_name_entry.pack(side=tk.LEFT, padx=(0, 5))
        self.chat_name_entry.bind("<Return>", lambda e: self._save_chat())

        tk.Button(chat_toolbar, text="SAVE", command=self._save_chat, width=6).pack(side=tk.LEFT, padx=(0, 15))

        tk.Label(chat_toolbar, text="Load Chat", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self._chat_combo_var = tk.StringVar()
        self._chat_combo = ttk.Combobox(
            chat_toolbar, textvariable=self._chat_combo_var, state="readonly",
            font=("Arial", 10), width=20
        )
        self._chat_combo.pack(side=tk.LEFT, padx=(0, 5))
        self._chat_combo.bind("<<ComboboxSelected>>", lambda e: self._load_chat())

        self._refresh_chat_list()

        # Chat display
        self.chat_display = tk.Text(
            self.root, wrap=tk.WORD, state="disabled", font=("Arial", 11)
        )
        self.chat_display.grid(row=3, column=0, sticky="nsew", padx=(10, 0), pady=10)

        # Scrollbar
        scrollbar = tk.Scrollbar(self.root, command=self.chat_display.yview)
        scrollbar.grid(row=3, column=1, sticky="ns", pady=10, padx=(0, 10))
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
        self.chat_display.tag_config(
            "cost_info", foreground="#0277bd", font=(MONO_FONT, 9)
        )

        # Input field
        self.input_field = tk.Text(
            self.root, height=3, wrap=tk.WORD, font=("Arial", 11)
        )
        self.input_field.grid(
            row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 5)
        )
        self.input_field.bind("<Return>", self.on_enter_key)
        self.input_field.focus_set()

        # Button bar
        button_frame = tk.Frame(self.root)
        button_frame.grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self.attach_button = tk.Button(
            button_frame, text="Attach Images", command=self.attach_image, width=14
        )
        self.attach_button.pack(side=tk.LEFT, padx=(5, 5))


        self.prompt_button = tk.Button(
            button_frame, text="System Prompt", command=self.open_prompt_editor, width=14
        )
        self.prompt_button.pack(side=tk.LEFT, padx=(5, 5))

        on_count = sum(1 for s in self.skills.values() if s.get("mode") == "enabled")
        od_count = sum(1 for s in self.skills.values() if s.get("mode") == "on_demand")
        if on_count and od_count:
            skills_label = f"Skills ({on_count}+{od_count})"
        elif on_count:
            skills_label = f"Skills ({on_count})"
        elif od_count:
            skills_label = f"Skills (0+{od_count})"
        else:
            skills_label = "Skills"
        self.skills_button = tk.Button(
            button_frame, text=skills_label, command=self.open_skills_editor, padx=10
        )
        self.skills_button.pack(side=tk.LEFT, padx=(5, 5))

        self.ps_safety_button = tk.Button(
            button_frame, text="Safety", command=self._open_ps_safety_dialog, padx=10
        )
        self.ps_safety_button.pack(side=tk.LEFT, padx=(0, 5))
        self._update_ps_safety_button()

        # Checkbox row (below buttons)
        checkbox_frame = tk.Frame(self.root)
        checkbox_frame.grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 5))

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

        # Tool row (below the Debug/display-toggle row) — Desktop / Browser plus the
        # MyAgent-style subsystems Meta / MCP / Google / IMAP / Outlook and the
        # SelfBot-native Pause (rest-the-self-chat) tool. Anthropic-only; each is
        # disabled when its optional libraries are absent (Meta and Pause need none).
        # Proton is labelled IMAP. Left-aligned (sticky="w") to match the Debug row above.
        tools_frame = tk.Frame(self.root)
        tools_frame.grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 5))

        self.desktop_toggle = tk.Checkbutton(
            tools_frame, text="Desktop", variable=self.desktop_enabled,
            font=("Arial", 9),
        )
        self.desktop_toggle.pack(side=tk.LEFT, padx=(5, 0))
        if not _HAS_DESKTOP:
            self.desktop_enabled.set(False)
            self.desktop_toggle.config(state=tk.DISABLED)

        self.browser_toggle = tk.Checkbutton(
            tools_frame, text="Browser", variable=self.browser_enabled,
            font=("Arial", 9),
        )
        self.browser_toggle.pack(side=tk.LEFT, padx=(5, 0))

        self.meta_toggle = tk.Checkbutton(
            tools_frame, text="Meta", variable=self.meta_enabled, font=("Arial", 9),
        )
        self.meta_toggle.pack(side=tk.LEFT, padx=(5, 0))

        self.mcp_toggle = tk.Checkbutton(
            tools_frame, text="MCP", variable=self.mcp_enabled, font=("Arial", 9),
        )
        self.mcp_toggle.pack(side=tk.LEFT, padx=(5, 0))
        if not (_HAS_MYAGENT_TOOLS and _HAS_MCP):
            self.mcp_enabled.set(False)
            self.mcp_toggle.config(state=tk.DISABLED)

        self.google_toggle = tk.Checkbutton(
            tools_frame, text="Google", variable=self.google_enabled, font=("Arial", 9),
        )
        self.google_toggle.pack(side=tk.LEFT, padx=(5, 0))
        if not (_HAS_MYAGENT_TOOLS and _HAS_GOOGLE):
            self.google_enabled.set(False)
            self.google_toggle.config(state=tk.DISABLED)

        self.proton_toggle = tk.Checkbutton(
            tools_frame, text="IMAP", variable=self.proton_enabled, font=("Arial", 9),
        )
        self.proton_toggle.pack(side=tk.LEFT, padx=(5, 0))
        if not (_HAS_MYAGENT_TOOLS and _HAS_PROTONMAIL):
            self.proton_enabled.set(False)
            self.proton_toggle.config(state=tk.DISABLED)

        self.outlook_toggle = tk.Checkbutton(
            tools_frame, text="Outlook", variable=self.outlook_enabled, font=("Arial", 9),
        )
        self.outlook_toggle.pack(side=tk.LEFT, padx=(5, 0))
        if not (_HAS_MYAGENT_TOOLS and _HAS_OUTLOOK):
            self.outlook_enabled.set(False)
            self.outlook_toggle.config(state=tk.DISABLED)

        self.pause_toggle = tk.Checkbutton(
            tools_frame, text="Pause", variable=self.pause_enabled, font=("Arial", 9),
        )
        self.pause_toggle.pack(side=tk.LEFT, padx=(5, 0))

        # Attachment indicator (hidden until an image is attached)
        self.attach_label = tk.Label(
            self.root, text="", foreground="#6a1b9a", font=("Arial", 9)
        )
        self.attach_label.grid(row=8, column=0, columnspan=2)

    # --- App State Persistence ---

    def _fetch_available_models(self):
        """Fetch available models from the Anthropic API — hiding deprecated /
        soon-to-be-retired ids — and fall back to the hardcoded list."""
        try:
            response = self.client.models.list(limit=100)
            # Build {id: display_name} mapping and id list
            self._model_display_names = {}
            model_ids = []
            for m in response.data:
                if m.id.startswith(DEPRECATED_MODEL_PREFIXES):
                    continue  # skip deprecated / soon-to-be-retired models
                self._model_display_names[m.id] = m.display_name
                model_ids.append(m.id)
            return model_ids if model_ids else FALLBACK_MODELS
        except Exception:
            self._model_display_names = {}
            return list(FALLBACK_MODELS)

    def _on_model_selected(self, event=None):
        # Map display name back to model ID
        selected_display = self._model_var.get()
        for mid in self._model_id_list:
            if self._model_display_names.get(mid, mid) == selected_display:
                self.model = mid
                break
        # Update thinking controls for new model
        support = self._model_supports_thinking()
        if support == "adaptive":
            # Hide checkbox + strength, show mode combobox
            self._thinking_check.pack_forget()
            self._thinking_strength_combo.pack_forget()
            self._thinking_mode_label.pack(side=tk.LEFT, padx=(10, 2))
            self._thinking_mode_combo.pack(side=tk.LEFT, padx=(0, 10))
            # Per-model values: always-on models (Fable/Mythos 5) drop "Off";
            # Xhigh needs Opus 4.7+ / Sonnet 5+, Max needs Opus 4.6+ / Sonnet 4.6+.
            values = self._anthropic_mode_values()
            self._thinking_mode_combo["values"] = values
            if self._thinking_mode_var.get() not in values:
                # "Off" on an always-on model coerces to Adaptive; an unsupported
                # Xhigh/Max coerces down to High.
                coerced = "Adaptive" if self._thinking_mode_var.get() == "Off" else "High"
                self._thinking_mode_var.set(coerced)
            self._on_thinking_mode_changed()
        elif support == "manual":
            # Hide mode combobox, show checkbox + strength
            self._thinking_mode_label.pack_forget()
            self._thinking_mode_combo.pack_forget()
            self._thinking_check.pack(side=tk.LEFT, padx=(10, 2))
            self._thinking_strength_combo.pack(side=tk.LEFT, padx=(0, 10))
            self._thinking_check.config(state="normal")
            self._update_thinking_strength_options()
            if self.thinking_enabled:
                self._on_thinking_toggled()
            else:
                self._temp_label.config(state="normal")
                self._temp_spin.config(state="normal")
                self._thinking_strength_combo.config(state="disabled")
        else:
            # Non-thinking model — hide mode combobox, show checkbox + strength disabled
            self._thinking_mode_label.pack_forget()
            self._thinking_mode_combo.pack_forget()
            self._thinking_check.pack(side=tk.LEFT, padx=(10, 2))
            self._thinking_strength_combo.pack(side=tk.LEFT, padx=(0, 10))
            self._thinking_var.set(False)
            self.thinking_enabled = False
            self.thinking_mode = "off"
            self._thinking_check.config(state="disabled")
            self._thinking_strength_combo.config(state="disabled")
            self._temp_label.config(state="normal")
            self._temp_spin.config(state="normal")
        self._save_last_state()

    def _on_temp_changed(self):
        try:
            val = self._temp_var.get()
            self.temperature = max(0.0, min(1.0, val))
        except (tk.TclError, ValueError):
            self.temperature = 1.0
        self._temp_var.set(self.temperature)
        self._save_last_state()

    @staticmethod
    def _parse_claude_major_minor(mid, families):
        """Parse the (major, minor) version tuple from a
        claude-<family>-<major>[-<minor>] id, for the first family prefix in
        ``families`` that ``mid`` starts with. None if no family matches or the
        major isn't an integer. A missing/non-numeric minor parses as 0 — the
        Claude 5 generation dropped the minor (claude-sonnet-5 -> (5, 0)), and
        dated snapshots keep working (the date lands in the minor slot, so
        (5, 20260601) >= (5, 0))."""
        for family in families:
            if mid.startswith(family):
                parts = mid[len(family):].split("-")
                try:
                    major = int(parts[0])
                except (ValueError, IndexError):
                    return None
                try:
                    minor = int(parts[1]) if len(parts) > 1 else 0
                except ValueError:
                    minor = 0
                return (major, minor)
        return None

    def _is_always_on_thinking(self, model_id=None):
        """Fable 5 / Mythos 5: thinking is always on (disable / budget_tokens are
        HTTP 400) and sampling params are rejected unconditionally."""
        mid = model_id or self.model or ""
        return mid.startswith(ALWAYS_ON_THINKING_PREFIXES)

    def _is_adaptive_model(self, model_id=None):
        """Adaptive-thinking models — Opus/Sonnet 4.6+ and the always-on Mythos
        class. Version-parsed so dated snapshots and future minors are caught
        without editing ADAPTIVE_THINKING_MODELS."""
        mid = model_id or self.model or ""
        if self._is_always_on_thinking(mid):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-opus-", "claude-sonnet-"))
        return version is not None and version >= (4, 6)

    def _thinking_on_by_default(self, model_id=None):
        """Models that run ADAPTIVE thinking when `thinking` is omitted — Opus
        5+, Sonnet 5+, and the always-on class. For these, "Off" must be sent as
        an explicit thinking={"type": "disabled"} or the model silently thinks
        against the non-thinking max_tokens cap. (Opus 5 accepts the explicit
        disable only at effort high or below — satisfied, "Off" sends no
        effort.)"""
        mid = model_id or self.model or ""
        if self._is_always_on_thinking(mid):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-opus-", "claude-sonnet-"))
        return version is not None and version >= (5, 0)

    def _rejects_temperature(self, model_id=None):
        """Models that removed sampling params — Opus 4.7+, Sonnet 5+, and the
        always-on Mythos class (a non-default temperature returns HTTP 400)."""
        mid = model_id or self.model or ""
        if self._is_always_on_thinking(mid):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-opus-",))
        if version is not None and version >= (4, 7):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-sonnet-",))
        return version is not None and version >= (5, 0)

    def _supports_max_effort(self, model_id=None):
        """'max' thinking effort — Opus 4.6+, Sonnet 4.6+ (incl. Sonnet 5), and
        the always-on Mythos class."""
        mid = model_id or self.model or ""
        if self._is_always_on_thinking(mid):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-opus-", "claude-sonnet-"))
        return version is not None and version >= (4, 6)

    def _supports_xhigh_effort(self, model_id=None):
        """'xhigh' thinking effort (between high and max) — Opus 4.7+, Sonnet 5+,
        and the always-on Mythos class. Sonnet 4.6 does NOT support it."""
        mid = model_id or self.model or ""
        if self._is_always_on_thinking(mid):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-opus-",))
        if version is not None and version >= (4, 7):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-sonnet-",))
        return version is not None and version >= (5, 0)

    def _anthropic_mode_values(self, model_id=None):
        """Thinking-mode combobox values for an adaptive model: always-on models
        (Fable/Mythos 5) drop "Off"; Xhigh and Max appear only where accepted."""
        values = [] if self._is_always_on_thinking(model_id) else ["Off"]
        values += ["Adaptive", "Low", "Medium", "High"]
        if self._supports_xhigh_effort(model_id):
            values.append("Xhigh")
        if self._supports_max_effort(model_id):
            values.append("Max")
        return values

    def _set_temp_state(self, state):
        """Enable ('normal') or disable the temperature label + spinbox together."""
        self._temp_label.config(state=state)
        self._temp_spin.config(state=state)

    def _model_supports_thinking(self, model_id=None):
        mid = model_id or self.model
        # Exact-match set catches undated aliases; the version-parsed helper
        # backstops dated snapshots and future Opus/Sonnet 4.6+ minors.
        if mid in ADAPTIVE_THINKING_MODELS or self._is_adaptive_model(mid):
            return "adaptive"
        for prefix in MANUAL_THINKING_PREFIXES:
            if mid.startswith(prefix):
                return "manual"
        return None

    def _on_thinking_toggled(self):
        self.thinking_enabled = self._thinking_var.get()
        if self.thinking_enabled:
            self._temp_label.config(state="disabled")
            self._temp_spin.config(state="disabled")
            self._update_thinking_strength_options()
            self._thinking_strength_combo.config(state="readonly")
        else:
            self._temp_label.config(state="normal")
            self._temp_spin.config(state="normal")
            self._thinking_strength_combo.config(state="disabled")
        self._save_last_state()

    def _on_thinking_mode_changed(self):
        val = self._thinking_mode_var.get()
        if val == "Off":
            self.thinking_enabled = False
            self.thinking_mode = "off"
            # Opus 4.7+, Sonnet 5+, and Fable/Mythos reject temperature even with
            # thinking off — keep the spinbox disabled for them (it's never sent).
            self._set_temp_state("disabled" if self._rejects_temperature() else "normal")
        elif val == "Adaptive":
            self.thinking_enabled = True
            self.thinking_mode = "adaptive"
            self.thinking_effort = "adaptive"
            self._set_temp_state("disabled")
        else:
            self.thinking_enabled = True
            self.thinking_mode = val.lower()
            self.thinking_effort = val.lower()
            self._set_temp_state("disabled")
        self._save_last_state()

    def _update_thinking_strength_options(self):
        support = self._model_supports_thinking()
        if support == "adaptive":
            values = list(EFFORT_LEVELS)
            self._thinking_strength_combo["values"] = values
            if self._thinking_strength_var.get() not in values:
                self._thinking_strength_var.set(self.thinking_effort if self.thinking_effort in values else "high")
        elif support == "manual":
            values = list(BUDGET_PRESETS.keys())
            self._thinking_strength_combo["values"] = values
            # Find matching preset for current budget
            current = self._thinking_strength_var.get()
            if current not in values:
                # Find closest preset
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

    def _get_user_label(self):
        name = self.my_name_entry.get().strip()
        return name if name else "You"

    def _get_friend_label(self):
        name = self.my_friend_entry.get().strip()
        return name if name else "Claude"

    def _update_title(self):
        # " — synced" = prompts/skills live in the OneDrive-shared dir (same
        # at-a-glance signal as TodoList). Appended, so Windows peer detection
        # (pygetwindow substring match on "Claude SelfBot") is unaffected.
        synced = " — synced" if STORES_SYNCED else ""
        if self.system_prompt_name:
            self.root.title(f"Claude SelfBot — {self.system_prompt_name}{synced}")
        else:
            self.root.title(f"Claude SelfBot{synced}")

    def _load_last_state(self):
        """Restore the last-used system prompt and window geometry on startup."""
        # Instance 2: bootstrap from instance 1's state if own file doesn't exist
        if self._is_second_instance and not os.path.exists(self._state_file):
            load_file = APP_STATE_FILE
        else:
            load_file = self._state_file
        if not os.path.exists(load_file):
            return
        try:
            with open(load_file, encoding="utf-8") as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        prompt_name = state.get("last_system_prompt_name", "")
        # Load unconditionally so legacy flat prompts migrate to the bundled dict form
        # on every launch, not just when a prompt name is remembered.
        prompts = self._load_saved_prompts()
        if prompt_name and prompt_name in prompts:
            self.system_prompt = self._prompt_entry_text(prompts[prompt_name])
            self.system_prompt_name = prompt_name
            self._update_title()
        model = state.get("last_model", "")
        if model and model in self.available_models:
            self.model = model
            self._model_var.set(self._model_display_names.get(model, model))
        temp = state.get("temperature")
        if temp is not None:
            self.temperature = max(0.0, min(1.0, float(temp)))
            self._temp_var.set(self.temperature)
        # Restore thinking settings
        self.thinking_enabled = state.get("thinking_enabled", False)
        self.thinking_effort = state.get("thinking_effort", "high")
        self.thinking_budget = state.get("thinking_budget", 8192)
        # Derive thinking_mode from persisted fields
        if self.thinking_enabled:
            self.thinking_mode = self.thinking_effort
        else:
            self.thinking_mode = "off"
        self._thinking_var.set(self.thinking_enabled)
        self._thinking_mode_var.set(self.thinking_mode.capitalize() if self.thinking_mode != "off" else "Off")
        support = self._model_supports_thinking()
        if support == "manual":
            for k, v in BUDGET_PRESETS.items():
                if v == self.thinking_budget:
                    self._thinking_strength_var.set(k)
                    break
        self._on_model_selected()
        # Restore save_thinking setting
        if "save_thinking" in state:
            self.save_thinking.set(state["save_thinking"])
        # Restore the tool-row toggles (only when their libs are available, so a
        # saved-on flag can't re-enable a checkbox that is disabled here).
        if state.get("desktop_enabled") and _HAS_DESKTOP:
            self.desktop_enabled.set(True)
        if state.get("browser_enabled"):
            self.browser_enabled.set(True)
        if state.get("meta_enabled"):
            self.meta_enabled.set(True)
        if _HAS_MYAGENT_TOOLS and _HAS_MCP and state.get("mcp_enabled"):
            self.mcp_enabled.set(True)
        if _HAS_MYAGENT_TOOLS and _HAS_GOOGLE and state.get("google_enabled"):
            self.google_enabled.set(True)
        if _HAS_MYAGENT_TOOLS and _HAS_PROTONMAIL and state.get("proton_enabled"):
            self.proton_enabled.set(True)
        if _HAS_MYAGENT_TOOLS and _HAS_OUTLOOK and state.get("outlook_enabled"):
            self.outlook_enabled.set(True)
        if state.get("pause_enabled"):
            self.pause_enabled.set(True)
        # Restore the Skills Manager dialog geometry (applied when it next opens).
        if state.get("skills_dialog_geometry"):
            self._last_skills_dialog_geometry = state["skills_dialog_geometry"]
        # Restore the Safety dialog's confirm-bypass set + geometry.
        self._disabled_confirm_patterns = set(state.get("disabled_confirm_patterns", []))
        if state.get("ps_safety_dialog_geometry"):
            self._last_ps_safety_geometry = state["ps_safety_dialog_geometry"]
        self._update_ps_safety_button()
        # Restore delay setting
        saved_delay = state.get("delay_seconds")
        if saved_delay is not None:
            self._delay_seconds = max(0, min(30, int(saved_delay)))
            if not self._is_second_instance:
                self._delay_var.set(self._delay_seconds)
        # Restore name fields
        # Instance 2: always read names from instance 1's state and swap them
        if self._is_second_instance:
            try:
                with open(APP_STATE_FILE, encoding="utf-8") as f:
                    i1_state = json.load(f)
                my_name = i1_state.get("my_friend", "")
                my_friend = i1_state.get("my_name", "")
            except (OSError, json.JSONDecodeError):
                my_name = state.get("my_friend", "")
                my_friend = state.get("my_name", "")
        else:
            my_name = state.get("my_name", "")
            my_friend = state.get("my_friend", "")
        self.my_name_entry.delete(0, tk.END)
        self.my_friend_entry.delete(0, tk.END)
        if my_name:
            self.my_name_entry.insert(0, my_name)
        if my_friend:
            self.my_friend_entry.insert(0, my_friend)
        # Instance 2: make name fields read-only
        if self._is_second_instance:
            self.my_name_entry.config(state="readonly")
            self.my_friend_entry.config(state="readonly")
        # Make the current system prompt authoritative for the layout on startup.
        # The independent last-live-state restores above cover the no-prompt case and
        # non-bundled keys (save_thinking, delay, geometry), but they do NOT carry the
        # prompt's per-skill modes and can drift from the prompt if a toggle was tweaked
        # without re-saving. Re-applying the current prompt's bundled environment on top
        # makes the skills AND the rest of the layout reflect the loaded prompt. Names are
        # guarded inside _apply_prompt_settings for the 2nd instance, so the swap survives;
        # a legacy text-only prompt has no bundled keys, so this is a safe no-op there.
        if prompt_name and prompt_name in prompts:
            self._apply_prompt_settings(prompts[prompt_name])
        # Restore window geometry — duo and solo modes are independent
        if self._duo_mode:
            # Launched from LaunchSelfBot.bat — restore saved duo geometry or default side-by-side
            duo_geo = state.get("duo_geometry", "")
            if duo_geo:
                m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", duo_geo)
                if m:
                    w, h, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                    cur_sw = self.root.winfo_screenwidth()
                    cur_sh = self.root.winfo_screenheight()
                    if x < cur_sw and y < cur_sh and x + w > 0 and y + h > 0 and w >= 400 and h >= 300:
                        self.root.geometry(duo_geo)
                        return
            # No saved duo geometry — calculate side-by-side from work area
            if IS_WINDOWS:
                rect = ctypes.wintypes.RECT()
                ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
                wa_x, wa_y = rect.left, rect.top
                wa_w, wa_h = rect.right - rect.left, rect.bottom - rect.top
            else:
                wa_x, wa_y = 0, 0
                wa_w = self.root.winfo_screenwidth()
                wa_h = self.root.winfo_screenheight()
            half_w = wa_w // 2
            if self._is_second_instance:
                self.root.geometry(f"{half_w}x{wa_h}+{wa_x + half_w}+{wa_y}")
            else:
                self.root.geometry(f"{half_w}x{wa_h}+{wa_x}+{wa_y}")
        else:
            # Manual launch — restore saved solo geometry if display setup hasn't changed
            geometry = state.get("geometry", "")
            saved_sw = state.get("screen_width", 0)
            saved_sh = state.get("screen_height", 0)
            geo_re = r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)"
            if geometry and saved_sw and saved_sh:
                cur_sw = self.root.winfo_screenwidth()
                cur_sh = self.root.winfo_screenheight()
                if saved_sw == cur_sw and saved_sh == cur_sh:
                    m = re.match(geo_re, geometry)
                    if m:
                        w, h, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                        if x < cur_sw and y < cur_sh and x + w > 0 and y + h > 0 and w >= 400 and h >= 300:
                            # A manually-opened 2nd instance restores the SAME solo geometry
                            # as instance 1 and lands exactly on top of it — which looks like
                            # nothing opened (or a crash). If its saved position (nearly)
                            # coincides with instance 1's, cascade it down-right so it's a
                            # visibly separate window. Cascade only on collision, so manual
                            # placement is preserved and the offset can't drift across launches.
                            if self._is_second_instance:
                                i1x = i1y = None
                                try:
                                    with open(APP_STATE_FILE, encoding="utf-8") as f:
                                        im = re.match(geo_re, json.load(f).get("geometry", ""))
                                    if im:
                                        i1x, i1y = int(im.group(3)), int(im.group(4))
                                except (OSError, json.JSONDecodeError):
                                    pass
                                if i1x is not None and abs(x - i1x) < 30 and abs(y - i1y) < 30:
                                    # Fixed cascade off instance 1 — no clamp against
                                    # winfo_screenwidth/height, which report only the PRIMARY
                                    # monitor and would yank instance 2 onto it when the pair
                                    # lives on a secondary display. A 60 px shift keeps a
                                    # window that already passed the on-screen validity check
                                    # on the same monitor and still reachable.
                                    x, y = x + CASCADE_OFFSET, y + CASCADE_OFFSET
                                    geometry = f"{w}x{h}+{x}+{y}"
                            self.root.update_idletasks()
                            self.root.geometry(geometry)
            elif self._is_second_instance:
                # No usable saved solo geometry — still offset instance 2 off the default
                # top-left position so it doesn't stack on instance 1.
                self.root.update_idletasks()
                self.root.geometry(f"+{CASCADE_OFFSET}+{CASCADE_OFFSET}")

    def _retry_load_names(self):
        """Instance 2: retry reading names from instance 1's state if they were empty."""
        if not self._is_second_instance:
            return
        current_name = self.my_name_entry.get().strip()
        current_friend = self.my_friend_entry.get().strip()
        if current_name and current_friend:
            return  # already populated
        try:
            with open(APP_STATE_FILE, encoding="utf-8") as f:
                i1_state = json.load(f)
            my_name = i1_state.get("my_friend", "")
            my_friend = i1_state.get("my_name", "")
            if my_name or my_friend:
                self.my_name_entry.config(state="normal")
                self.my_friend_entry.config(state="normal")
                self.my_name_entry.delete(0, tk.END)
                self.my_friend_entry.delete(0, tk.END)
                if my_name:
                    self.my_name_entry.insert(0, my_name)
                if my_friend:
                    self.my_friend_entry.insert(0, my_friend)
                self.my_name_entry.config(state="readonly")
                self.my_friend_entry.config(state="readonly")
                return
        except (OSError, json.JSONDecodeError):
            pass
        # Still empty — keep retrying every 2 seconds
        self.root.after(2000, self._retry_load_names)

    def _save_last_state(self):
        """Persist the current system prompt name and window geometry for next startup."""
        state = {
            "last_system_prompt_name": self.system_prompt_name,
            "last_model": self.model,
            "temperature": self.temperature,
            "thinking_enabled": self.thinking_enabled,
            "thinking_effort": self.thinking_effort,
            "thinking_budget": self.thinking_budget,
            "save_thinking": self.save_thinking.get(),
            "my_name": self.my_name_entry.get(),
            "my_friend": self.my_friend_entry.get(),
            "delay_seconds": self._delay_seconds,
            "desktop_enabled": self.desktop_enabled.get(),
            "browser_enabled": self.browser_enabled.get(),
            "meta_enabled": self.meta_enabled.get(),
            "mcp_enabled": self.mcp_enabled.get(),
            "google_enabled": self.google_enabled.get(),
            "proton_enabled": self.proton_enabled.get(),
            "outlook_enabled": self.outlook_enabled.get(),
            "pause_enabled": self.pause_enabled.get(),
        }
        # Skills Manager dialog geometry — live value if open, else the last-known one.
        if self.skills_editor_window and self.skills_editor_window.winfo_exists():
            state["skills_dialog_geometry"] = self.skills_editor_window.geometry()
        elif getattr(self, "_last_skills_dialog_geometry", None):
            state["skills_dialog_geometry"] = self._last_skills_dialog_geometry
        # Safety dialog: the confirm-bypass set + its geometry (live if open).
        state["disabled_confirm_patterns"] = sorted(self._disabled_confirm_patterns)
        if self._ps_safety_dialog and self._ps_safety_dialog.winfo_exists():
            state["ps_safety_dialog_geometry"] = self._ps_safety_dialog.geometry()
        elif getattr(self, "_last_ps_safety_geometry", None):
            state["ps_safety_dialog_geometry"] = self._last_ps_safety_geometry
        # Load existing state to preserve the other mode's geometry
        try:
            with open(self._state_file, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, json.JSONDecodeError):
            existing = {}
        # Preserve whichever geometry key belongs to the other mode
        if self._duo_mode:
            state["duo_geometry"] = self.root.geometry()
            # Keep solo geometry from existing state
            for k in ("geometry", "screen_width", "screen_height"):
                if k in existing:
                    state[k] = existing[k]
        else:
            state["geometry"] = self.root.geometry()
            state["screen_width"] = self.root.winfo_screenwidth()
            state["screen_height"] = self.root.winfo_screenheight()
            # Keep duo geometry from existing state
            if "duo_geometry" in existing:
                state["duo_geometry"] = existing["duo_geometry"]
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def _periodic_save(self):
        """Auto-save state and chat every 5 seconds so force-kill doesn't lose data."""
        try:
            self._save_last_state()
        except Exception:
            pass
        # Periodically auto-save the chat so a force-kill doesn't lose data
        if self.messages:
            try:
                msg_count = len(self.messages)
                if msg_count != getattr(self, '_last_autosaved_msg_count', 0):
                    self._auto_save_on_close()
                    self._last_autosaved_msg_count = msg_count
            except Exception:
                pass
        self.root.after(5000, self._periodic_save)

    # --- System Prompt Editor ---

    def _load_saved_prompts(self):
        # Tolerant read (a half-synced OneDrive write must not crash the app);
        # also runs the one-shot repo-root→OneDrive migration on first call.
        prompts = _load_store(PROMPTS_FILE)
        migrated = _absorb_conflict_forks(PROMPTS_FILE, prompts)
        # Migrate legacy flat {name: "text"} entries → {name: {"text": "..."}} so each
        # prompt can bundle a full environment (names, model, skills, tools, safety).
        for pname, entry in list(prompts.items()):
            if not isinstance(entry, dict):
                prompts[pname] = {"text": entry if isinstance(entry, str) else ""}
                migrated = True
        if "Default" not in prompts:
            if not prompts and os.path.exists(PROMPTS_FILE):
                # Exists but unreadable — serve a session-only Default rather
                # than overwriting a possibly half-synced store; retry next launch.
                return {"Default": {"text": DEFAULT_SYSTEM_PROMPT}}
            prompts["Default"] = {"text": DEFAULT_SYSTEM_PROMPT}
            migrated = True
        if migrated:
            self._save_prompts_to_disk(prompts)
        return prompts

    def _save_prompts_to_disk(self, prompts):
        _save_store(PROMPTS_FILE, prompts)

    @staticmethod
    def _prompt_entry_text(entry):
        """A saved prompt is the new dict form ({"text": ..., <settings>}) or a legacy
        bare string. Return its text either way."""
        if isinstance(entry, dict):
            return entry.get("text", "")
        return entry or ""

    def _capture_prompt_settings(self):
        """Snapshot the current main-screen environment for bundling into a saved
        system prompt: names, model params, the tool-row toggles, per-skill modes, and
        the Safety confirm-bypass set. The Debug/display toggle row stays global."""
        return {
            "my_name": self.my_name_entry.get(),
            "my_friend": self.my_friend_entry.get(),
            "model": self.model,
            "temperature": self.temperature,
            "thinking_enabled": self.thinking_enabled,
            "thinking_effort": self.thinking_effort,
            "thinking_budget": self.thinking_budget,
            "thinking_mode": self.thinking_mode,
            "desktop": self.desktop_enabled.get(),
            "browser": self.browser_enabled.get(),
            "meta": self.meta_enabled.get(),
            "mcp": self.mcp_enabled.get(),
            "google": self.google_enabled.get(),
            "proton": self.proton_enabled.get(),
            "outlook": self.outlook_enabled.get(),
            "pause": self.pause_enabled.get(),
            "skill_modes": {n: s.get("mode", "disabled") for n, s in self.skills.items()},
            "disabled_confirm_patterns": sorted(self._disabled_confirm_patterns),
        }

    def _apply_prompt_settings(self, entry):
        """Apply a saved prompt's bundled environment to the live main screen. Only keys
        present in the entry are applied, so a legacy text-only prompt leaves the current
        environment untouched."""
        if not isinstance(entry, dict):
            return
        if "my_name" in entry and not self._is_second_instance:
            self.my_name_entry.delete(0, tk.END)
            self.my_name_entry.insert(0, entry["my_name"])
        if "my_friend" in entry and not self._is_second_instance:
            self.my_friend_entry.delete(0, tk.END)
            self.my_friend_entry.insert(0, entry["my_friend"])
        model = entry.get("model")
        if model and model in self.available_models:
            self.model = model
            self._model_var.set(self._model_display_names.get(model, model))
        if "temperature" in entry:
            try:
                self.temperature = max(0.0, min(1.0, float(entry["temperature"])))
                self._temp_var.set(self.temperature)
            except (TypeError, ValueError):
                pass
        if "thinking_enabled" in entry:
            self.thinking_enabled = bool(entry.get("thinking_enabled", False))
            self.thinking_effort = entry.get("thinking_effort", self.thinking_effort)
            self.thinking_budget = entry.get("thinking_budget", self.thinking_budget)
            self.thinking_mode = entry.get(
                "thinking_mode", self.thinking_effort if self.thinking_enabled else "off")
            self._thinking_var.set(self.thinking_enabled)
            self._thinking_mode_var.set(
                self.thinking_mode.capitalize() if self.thinking_mode != "off" else "Off")
            if self._model_supports_thinking() == "manual":
                for k, v in BUDGET_PRESETS.items():
                    if v == self.thinking_budget:
                        self._thinking_strength_var.set(k)
                        break
        # Tool-row toggles — respect the same _HAS_* gating as _load_last_state.
        if "desktop" in entry and _HAS_DESKTOP:
            self.desktop_enabled.set(bool(entry["desktop"]))
        if "browser" in entry:
            self.browser_enabled.set(bool(entry["browser"]))
        if "meta" in entry:
            self.meta_enabled.set(bool(entry["meta"]))
        if "mcp" in entry and _HAS_MYAGENT_TOOLS and _HAS_MCP:
            self.mcp_enabled.set(bool(entry["mcp"]))
        if "google" in entry and _HAS_MYAGENT_TOOLS and _HAS_GOOGLE:
            self.google_enabled.set(bool(entry["google"]))
        if "proton" in entry and _HAS_MYAGENT_TOOLS and _HAS_PROTONMAIL:
            self.proton_enabled.set(bool(entry["proton"]))
        if "outlook" in entry and _HAS_MYAGENT_TOOLS and _HAS_OUTLOOK:
            self.outlook_enabled.set(bool(entry["outlook"]))
        if "pause" in entry:
            self.pause_enabled.set(bool(entry["pause"]))
        if "disabled_confirm_patterns" in entry:
            self._disabled_confirm_patterns = set(entry["disabled_confirm_patterns"])
            self._update_ps_safety_button()
        if "skill_modes" in entry:
            self._restore_skill_modes(entry["skill_modes"])
        # Refresh the model-dependent thinking widgets for the (possibly new) model.
        self._on_model_selected()

    def _restore_skill_modes(self, saved):
        """Apply a prompt's saved skill modes to the live session ONLY — this does NOT
        rewrite the skills store (the sticky global source of truth), matching MyAgent.
        Skills absent from the snapshot fall back to disabled for this session, with a
        visible ⚠ naming them (a just-enabled skill silently vanishing from the system
        prompt cost a real debugging session, 2026-08-07)."""
        if not isinstance(saved, dict):
            return
        forced_off = []
        for sname in self.skills:
            mode = saved.get(sname)
            if mode is None:
                if self.skills[sname].get("mode") != "disabled":
                    forced_off.append(sname)
                self.skills[sname]["mode"] = "disabled"
            elif mode in ("disabled", "enabled", "on_demand"):
                self.skills[sname]["mode"] = mode
        if forced_off:
            names = ", ".join(f"'{n}'" for n in forced_off)
            self.queue.put({"type": "warning", "content":
                            f"⚠ Skill(s) {names}: ON in the skills store but absent from "
                            f"this prompt's skill_modes snapshot — disabled for this "
                            f"session. Re-save the prompt to include them.\n"})
        self._update_skills_button()
        if (self.skills_editor_window and self.skills_editor_window.winfo_exists()
                and self._skills_refresh_list):
            self._skills_refresh_list()

    def open_prompt_editor(self):
        if self.prompt_editor_window and self.prompt_editor_window.winfo_exists():
            self.prompt_editor_window.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("System Prompt Editor")
        win.geometry("650x500")
        win.transient(self.root)
        self.prompt_editor_window = win

        # Row 0: Save row
        tk.Label(win, text="Save System Prompt", font=("Arial", 10)).grid(
            row=0, column=0, padx=(10, 5), pady=(10, 5), sticky="w"
        )
        self._prompt_name_entry = tk.Entry(win, font=("Arial", 10), width=30)
        self._prompt_name_entry.grid(row=0, column=1, padx=5, pady=(10, 5), sticky="ew")

        tk.Button(win, text="SAVE", command=self._save_prompt, width=8).grid(
            row=0, column=2, padx=5, pady=(10, 5)
        )
        tk.Button(win, text="DELETE", command=self._delete_prompt, width=8).grid(
            row=0, column=3, padx=5, pady=(10, 5)
        )
        tk.Button(win, text="CLEAR", command=self._clear_prompt_editor, width=8).grid(
            row=0, column=4, padx=(5, 10), pady=(10, 5)
        )

        # Row 1: Load row
        tk.Label(win, text="Load System Prompt", font=("Arial", 10)).grid(
            row=1, column=0, padx=(10, 5), pady=5, sticky="w"
        )
        self._prompt_combo_var = tk.StringVar()
        self._prompt_combo = ttk.Combobox(
            win, textvariable=self._prompt_combo_var, state="readonly",
            font=("Arial", 10), width=28
        )
        self._prompt_combo.grid(row=1, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        self._prompt_combo.bind("<<ComboboxSelected>>", self._on_prompt_selected)
        self._refresh_prompt_list()

        # Row 2: Text editor
        self._prompt_text = tk.Text(win, wrap=tk.WORD, font=(MONO_FONT, 10))
        self._prompt_text.grid(
            row=2, column=0, columnspan=5, sticky="nsew", padx=10, pady=(5, 5)
        )

        # Scrollbar for text editor
        prompt_scrollbar = tk.Scrollbar(win, command=self._prompt_text.yview)
        prompt_scrollbar.grid(row=2, column=5, sticky="ns", pady=(5, 5), padx=(0, 5))
        self._prompt_text.config(yscrollcommand=prompt_scrollbar.set)

        # Row 3: Apply button
        tk.Button(
            win, text="Apply to Chat", command=self._apply_prompt,
            font=("Arial", 10, "bold"), width=16
        ).grid(row=3, column=0, columnspan=5, pady=(5, 10))

        # Grid weights
        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(2, weight=1)

        # Load current prompt into editor
        self._prompt_text.insert("1.0", self.system_prompt)
        if self.system_prompt_name:
            self._prompt_name_entry.insert(0, self.system_prompt_name)
            self._prompt_combo_var.set(self.system_prompt_name)

    def _refresh_prompt_list(self):
        prompts = self._load_saved_prompts()
        self._prompt_combo["values"] = list(prompts.keys())

    def _save_prompt(self):
        name = self._prompt_name_entry.get().strip()
        if not name:
            messagebox.showwarning("No name", "Enter a name for the prompt.", parent=self.prompt_editor_window)
            return
        text = self._prompt_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("Empty prompt", "The prompt text is empty.", parent=self.prompt_editor_window)
            return
        prompts = self._load_saved_prompts()
        # Bundle the current main-screen environment with the prompt text so loading
        # this prompt later restores names, model params, skills, tools and safety.
        prompts[name] = {"text": text, **self._capture_prompt_settings()}
        self._save_prompts_to_disk(prompts)
        self._refresh_prompt_list()
        self._prompt_combo_var.set(name)

    def _delete_prompt(self):
        name = self._prompt_combo_var.get()
        if not name:
            name = self._prompt_name_entry.get().strip()
        if not name:
            messagebox.showwarning("No selection", "Select or enter a prompt name to delete.", parent=self.prompt_editor_window)
            return
        prompts = self._load_saved_prompts()
        if name not in prompts:
            messagebox.showwarning("Not found", f"No saved prompt named '{name}'.", parent=self.prompt_editor_window)
            return
        prompts.pop(name)
        self._save_prompts_to_disk(prompts)
        self._refresh_prompt_list()
        self._prompt_combo_var.set("")
        self._prompt_name_entry.delete(0, tk.END)

    def _clear_prompt_editor(self):
        self._prompt_text.delete("1.0", tk.END)
        self._prompt_name_entry.delete(0, tk.END)
        self._prompt_combo_var.set("")

    def _on_prompt_selected(self, event):
        name = self._prompt_combo_var.get()
        prompts = self._load_saved_prompts()
        if name in prompts:
            entry = prompts[name]
            self._prompt_text.delete("1.0", tk.END)
            self._prompt_text.insert("1.0", self._prompt_entry_text(entry))
            self._prompt_name_entry.delete(0, tk.END)
            self._prompt_name_entry.insert(0, name)
            # Loading a saved prompt restores its bundled environment to the main screen.
            self._apply_prompt_settings(entry)

    def _apply_prompt(self):
        text = self._prompt_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("Empty prompt", "The prompt text is empty.", parent=self.prompt_editor_window)
            return
        self.system_prompt = text
        self.system_prompt_name = self._prompt_name_entry.get().strip()
        self._update_title()
        self._save_last_state()
        self.prompt_editor_window.destroy()

    # --- Skills System ---

    def _load_skills(self):
        # Per-skill SKILL.md tree (frontmatter: name/description/mode). Runs
        # the one-shot skills.json→tree migration and heals OneDrive per-file
        # conflict forks; every entry comes back with a valid mode.
        return _load_skills_tree(SKILLS_DIR)

    def _save_skills(self):
        # Diff-aware and WRITE-ONLY (never deletes folders) — deletion is an
        # explicit action via _delete_skill_tree_entry at the delete callsites.
        _save_skills_tree(SKILLS_DIR, self.skills)

    def _post_skill_ui_refresh(self):
        """Thread-safe refresh of the Skills button and the open Skills Manager listbox.
        Called from the streaming thread (do_manage_skills); marshals onto the Tk main
        thread via root.after so the in-dialog list repaints when the agent edits skills."""
        def _refresh():
            self._update_skills_button()
            if (self.skills_editor_window and self.skills_editor_window.winfo_exists()
                    and self._skills_refresh_list):
                self._skills_refresh_list()
        self.root.after(0, _refresh)

    def _sanitize_geometry(self, geo, min_w=400, min_h=300):
        """Validate a saved 'WxH+X+Y' geometry: enforce a minimum size and drop an
        off-screen position (letting the WM place it) — matching SelfBot's main-window
        geometry-restore checks."""
        m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geo or "")
        if not m:
            return f"{max(min_w, 900)}x{max(min_h, 500)}"
        w, h, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        w, h = max(w, min_w), max(h, min_h)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        if x < sw and y < sh and x + w > 0 and y + h > 0:
            return f"{w}x{h}+{x}+{y}"
        return f"{w}x{h}"

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
            pass  # Button doesn't exist yet or was destroyed

    @staticmethod
    def _format_on_demand_listing(skills):
        """Build the '## On-Demand Skills' system-prompt block: one bullet per
        on_demand skill carrying its description — the what-it-does / when-to-
        use-it routing signal, Agent-Skills style. A skill without a description
        is listed by bare name. Returns "" when nothing is on_demand. (In-file
        copy of SkillsMixin._format_on_demand_listing, like _trim_history_for_context.)"""
        lines = []
        for name, skill in skills.items():
            if skill.get("mode") != "on_demand":
                continue
            desc = (skill.get("description") or "").strip()
            lines.append(f"- {name} — {desc}" if desc else f"- {name}")
        if not lines:
            return ""
        return (
            "## On-Demand Skills\n"
            "The following skills are available via the `get_skill` tool. "
            "Call `get_skill` with the skill name when its description matches "
            "the task at hand:\n" + "\n".join(lines)
        )

    def _build_system_prompt(self):
        parts = [self.system_prompt]
        for name, skill in self.skills.items():
            if skill.get("mode") == "enabled":
                parts.append(f"## Skill: {name}\n{skill['content']}")
        od_block = self._format_on_demand_listing(self.skills)
        if od_block:
            parts.append(od_block)
        return "\n\n".join(parts)

    def open_skills_editor(self):
        if self.skills_editor_window and self.skills_editor_window.winfo_exists():
            self.skills_editor_window.lift()
            return

        win = tk.Toplevel(self.root)
        win.withdraw()  # Hide until geometry is set (avoids a flash at the wrong spot)
        win.title("Skills Manager")
        if IS_WINDOWS:
            win.transient(self.root)
        self.skills_editor_window = win

        def _on_skills_close():
            self._last_skills_dialog_geometry = win.geometry()
            self._save_last_state()
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_skills_close)

        # Top bar: name entry (expands) + SAVE / DELETE / NEW hugging the right edge
        top = tk.Frame(win)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 5))

        tk.Label(top, text="Skill Name", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        name_entry = tk.Entry(top, font=("Arial", 10), width=20)
        name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        def save_skill():
            name = name_entry.get().strip()
            if not name:
                messagebox.showwarning("No name", "Enter a name for the skill.", parent=win)
                return
            # Enforce Agent-Skills kebab-case on NEW names only (an existing
            # legacy-named skill stays editable); offer the auto-converted
            # name so a Title-Case typo is one click from correct.
            if name not in self.skills and not self._is_kebab_name(name):
                hint = self._kebabize(name)
                if not hint:
                    messagebox.showwarning(
                        "Invalid name",
                        "Skill names use Agent-Skills kebab-case: lowercase letters, "
                        "digits and hyphens (e.g. 'westpac-login').", parent=win)
                    return
                if hint in self.skills:
                    prompt = (f"Skill names use kebab-case, and '{hint}' already exists — "
                              f"SAVE will overwrite its content.\n\nSave as '{hint}'?")
                else:
                    prompt = ("Skill names use kebab-case (lowercase letters/digits/"
                              f"hyphens).\n\nCreate as '{hint}' instead?")
                if not messagebox.askyesno("Skill name", prompt, parent=win):
                    return
                name = hint
                name_entry.delete(0, tk.END)
                name_entry.insert(0, name)
            content = text_editor.get("1.0", "end-1c").strip()
            if not content:
                messagebox.showwarning("Empty", "The skill content is empty.", parent=win)
                return
            # Merge into the existing entry (never whole-entry replace) so fields
            # this editor doesn't show — e.g. one added by a newer version on
            # another machine — survive a SAVE here.
            entry = dict(self.skills.get(name, {}))
            entry["content"] = content
            entry.setdefault("mode", "disabled")
            desc = " ".join(desc_entry.get("1.0", "end-1c").split())
            if desc:
                entry["description"] = desc
            else:
                entry.pop("description", None)
            self.skills[name] = entry
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
                _delete_skill_tree_entry(SKILLS_DIR, name)  # _save_skills never deletes
                self._save_skills()
                refresh_list()
                name_entry.delete(0, tk.END)
                desc_entry.delete("1.0", tk.END)
                text_editor.delete("1.0", tk.END)
                self._update_skills_button()

        def new_skill():
            name_entry.delete(0, tk.END)
            desc_entry.delete("1.0", tk.END)
            text_editor.delete("1.0", tk.END)
            skill_listbox.selection_clear(0, tk.END)

        # Packed side=RIGHT in reverse order so the visual left-to-right order stays
        # SAVE, DELETE, NEW while the buttons hug the right edge; the name entry
        # (fill=X, expand) absorbs all the space in between.
        tk.Button(top, text="NEW", command=new_skill, width=5).pack(side=tk.RIGHT, padx=2)
        tk.Button(top, text="DELETE", command=delete_skill, width=7).pack(side=tk.RIGHT, padx=2)
        tk.Button(top, text="SAVE", command=save_skill, width=6).pack(side=tk.RIGHT, padx=2)

        # Description row: the what+when trigger signal shown in the system
        # prompt for on_demand skills (Agent-Skills style). Optional.
        desc_row = tk.Frame(win)
        desc_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 5))
        tk.Label(desc_row, text="Description", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5), anchor="n")
        # Three word-wrapped rows so long what+when descriptions are readable
        # (they routinely run to two sentences); newlines a user types here
        # are normalized to spaces on SAVE — descriptions are single-line.
        desc_entry = tk.Text(desc_row, font=("Arial", 10), height=3, wrap="word", undo=True)
        desc_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Left panel: Cycle Mode button ABOVE the listbox
        left = tk.Frame(win)
        left.grid(row=2, column=0, sticky="nsew", padx=(10, 5), pady=(0, 10))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        toggle_btn = tk.Button(left, text="Cycle Mode", font=("Arial", 9))
        toggle_btn.grid(row=0, column=0, sticky="w", pady=(0, 5))

        skill_listbox = tk.Listbox(left, font=("Arial", 10), width=40)
        skill_listbox.grid(row=1, column=0, sticky="nsew")
        list_scrollbar = tk.Scrollbar(left, command=skill_listbox.yview)
        list_scrollbar.grid(row=1, column=1, sticky="ns")
        skill_listbox.config(yscrollcommand=list_scrollbar.set)

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
            for i, sdata in enumerate(self.skills.values()):
                mode = sdata.get("mode", "disabled")
                if mode == "enabled":
                    skill_listbox.itemconfig(i, fg="#2e7d32")
                elif mode == "on_demand":
                    skill_listbox.itemconfig(i, fg="#1565c0")

        # Expose the refresher so tool-driven CRUD (do_manage_skills, running on the
        # streaming thread) can repaint this list via _post_skill_ui_refresh.
        self._skills_refresh_list = refresh_list

        def on_select(event):
            sel = skill_listbox.curselection()
            if not sel:
                return
            name = skill_listbox.get(sel[0])[5:]
            if name in self.skills:
                name_entry.delete(0, tk.END)
                name_entry.insert(0, name)
                desc_entry.delete("1.0", tk.END)
                desc_entry.insert("1.0", self.skills[name].get("description", ""))
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

        def _cycle_on_space(event):
            # Space bar mirrors the Cycle Mode button on the selected skill.
            toggle_skill()
            return "break"  # suppress Tk's default <space> select-active binding

        skill_listbox.bind("<<ListboxSelect>>", on_select)
        skill_listbox.bind("<space>", _cycle_on_space)
        toggle_btn.config(command=toggle_skill)

        # Right panel: content editor
        right = tk.Frame(win)
        right.grid(row=2, column=1, sticky="nsew", padx=(5, 10), pady=(0, 10))
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        text_editor = tk.Text(right, wrap=tk.WORD, font=(MONO_FONT, 10))
        text_editor.grid(row=0, column=0, sticky="nsew")
        text_scrollbar = tk.Scrollbar(right, command=text_editor.yview)
        text_scrollbar.grid(row=0, column=1, sticky="ns")
        text_editor.config(yscrollcommand=text_scrollbar.set)

        win.grid_columnconfigure(0, weight=0)
        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(2, weight=1)

        refresh_list()

        # Restore geometry AFTER content is laid out, then show (withdraw/deiconify).
        win.update_idletasks()
        saved_geo = getattr(self, '_last_skills_dialog_geometry', None)
        if saved_geo:
            win.geometry(self._sanitize_geometry(saved_geo, min_w=400, min_h=300))
        else:
            win.geometry("900x500")
        win.deiconify()

    # --- Chat Save / Load ---

    def _save_name(self, name):
        """Append '_' to save names for the second instance to avoid filename collisions."""
        if self._is_second_instance:
            return name + "_"
        return name

    @staticmethod
    def _sanitize_filename(name, ext='.json'):
        """Convert a chat name to a safe filename."""
        # \x00-\x1f: Windows open() rejects control characters — a multi-line
        # first user message otherwise puts \n into the auto-save filename.
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name)
        safe = safe.strip('. ')
        return (safe or '_') + ext

    @staticmethod
    def _chat_file_path(name):
        return os.path.join(CHATS_DIR, App._sanitize_filename(name))

    def _load_saved_chats(self):
        """Load all saved chats from individual files in saved_chats directory."""
        chats = {}
        if not os.path.isdir(CHATS_DIR):
            return chats
        for fname in os.listdir(CHATS_DIR):
            if not fname.endswith('.json'):
                continue
            fpath = os.path.join(CHATS_DIR, fname)
            try:
                with open(fpath, encoding='utf-8') as f:
                    data = json.load(f)
                name = data.get('name', fname[:-5])
                chats[name] = data
            except (json.JSONDecodeError, OSError):
                continue
        return chats

    def _load_single_chat(self, name):
        """Load a single chat by name."""
        fpath = self._chat_file_path(name)
        if not os.path.exists(fpath):
            return None
        try:
            with open(fpath, encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def _save_chat_file(self, name, data):
        """Save a single chat to its own file."""
        os.makedirs(CHATS_DIR, exist_ok=True)
        data['name'] = name
        fpath = self._chat_file_path(name)
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _refresh_chat_list(self):
        chats = self._load_saved_chats()
        self._chat_combo["values"] = sorted(chats.keys())

    @staticmethod
    def _clean_content_block(block):
        """Strip extra fields from a content block, keeping only API-valid fields."""
        if not isinstance(block, dict):
            return block
        btype = block.get("type")
        if btype == "text":
            return {"type": "text", "text": block.get("text", "")}
        if btype == "tool_use":
            return {"type": "tool_use", "id": block["id"], "name": block["name"], "input": block["input"]}
        if btype == "tool_result":
            cleaned = {"type": "tool_result", "tool_use_id": block["tool_use_id"]}
            if "content" in block:
                content = block["content"]
                if isinstance(content, list):
                    # Recursively clean sub-blocks, replacing images with placeholder
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
        """Convert messages to JSON-serializable format, stripping image data and extra fields.
        Thinking blocks are stripped unless the Save Thinking checkbox is enabled."""
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

    def _save_chat(self):
        name = self.chat_name_entry.get().strip()
        if not name:
            messagebox.showwarning("No name", "Enter a name for the chat.")
            return
        if not self.messages:
            messagebox.showwarning("Empty chat", "There is no chat to save.")
            return
        save_name = self._save_name(name)
        self._save_chat_file(save_name, {
            "messages": self._serialize_messages(),
            "tools": self._get_tools(),
            "system_prompt": self.system_prompt,
            "system_prompt_name": self.system_prompt_name,
            "model": self.model,
            "temperature": self.temperature,
            "thinking_enabled": self.thinking_enabled,
            "thinking_effort": self.thinking_effort,
            "thinking_budget": self.thinking_budget,
        })
        txt_path = os.path.join("saved_chats", self._sanitize_filename(save_name, '.txt'))
        output_text = self.chat_display.get("1.0", tk.END).rstrip()
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(output_text)
        self._refresh_chat_list()
        self._chat_combo_var.set(save_name)

    def _load_chat(self):
        name = self._chat_combo_var.get()
        if not name:
            return
        chat_data = self._load_single_chat(name)
        if chat_data is None:
            messagebox.showwarning("Not found", f"No saved chat named '{name}'.")
            return
        # Sanitize loaded messages — strip extra fields (e.g. parsed_output)
        # that the API rejects when sent back
        loaded = chat_data["messages"]
        for msg in loaded:
            content = msg.get("content")
            if isinstance(content, list):
                msg["content"] = [self._clean_content_block(b) for b in content]
        self.messages = loaded
        self.system_prompt = chat_data.get("system_prompt", DEFAULT_SYSTEM_PROMPT)
        self.system_prompt_name = chat_data.get("system_prompt_name", "")
        saved_model = chat_data.get("model", DEFAULT_MODEL)
        if saved_model in self.available_models:
            self.model = saved_model
        else:
            self.model = DEFAULT_MODEL
        self._model_var.set(self._model_display_names.get(self.model, self.model))
        saved_temp = chat_data.get("temperature")
        if saved_temp is not None:
            self.temperature = max(0.0, min(1.0, float(saved_temp)))
        else:
            self.temperature = 1.0
        self._temp_var.set(self.temperature)
        # Restore thinking settings from saved chat
        self.thinking_enabled = chat_data.get("thinking_enabled", False)
        self.thinking_effort = chat_data.get("thinking_effort", "high")
        self.thinking_budget = chat_data.get("thinking_budget", 8192)
        # Derive thinking_mode from persisted fields
        if self.thinking_enabled:
            self.thinking_mode = self.thinking_effort
        else:
            self.thinking_mode = "off"
        self._thinking_var.set(self.thinking_enabled)
        self._thinking_mode_var.set(self.thinking_mode.capitalize() if self.thinking_mode != "off" else "Off")
        support = self._model_supports_thinking()
        if support == "manual":
            for k, v in BUDGET_PRESETS.items():
                if v == self.thinking_budget:
                    self._thinking_strength_var.set(k)
                    break
        self._on_model_selected()
        self._update_title()
        self._save_last_state()
        self.chat_name_entry.delete(0, tk.END)
        self.chat_name_entry.insert(0, name)
        self._rebuild_display()

    def _delete_chat(self):
        name = self._chat_combo_var.get()
        if not name:
            name = self.chat_name_entry.get().strip()
        if not name:
            messagebox.showwarning("No selection", "Select or enter a chat name to delete.")
            return
        fpath = self._chat_file_path(name)
        if not os.path.exists(fpath):
            messagebox.showwarning("Not found", f"No saved chat named '{name}'.")
            return
        os.remove(fpath)
        # Also remove the associated .txt export if it exists
        txt_path = os.path.join(CHATS_DIR, self._sanitize_filename(name, '.txt'))
        try:
            os.remove(txt_path)
        except OSError:
            pass
        self._refresh_chat_list()
        self._chat_combo_var.set("")
        self.chat_name_entry.delete(0, tk.END)

    def _new_chat(self):
        self.messages = []
        self.pending_images.clear()
        self.update_attach_label()
        self.chat_display.config(state="normal")
        self.chat_display.delete("1.0", tk.END)
        self.chat_display.config(state="disabled")
        self.chat_name_entry.delete(0, tk.END)
        self._chat_combo_var.set("")

    def _rebuild_display(self):
        """Rebuild the chat display from loaded message history."""
        self.chat_display.config(state="normal")
        self.chat_display.delete("1.0", tk.END)
        for msg in self.messages:
            role = msg["role"]
            content = msg["content"]
            if role == "user" and isinstance(content, str):
                self.chat_display.insert(tk.END, f"{self._get_user_label()}:\n", "user_label")
                self.chat_display.insert(tk.END, content + "\n\n", "user")
            elif role == "user" and isinstance(content, list):
                # Skip tool_result blocks (internal API messages)
                if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                    continue
                texts = []
                has_images = False
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        t = block.get("text", "")
                        if t == "[Image was attached]":
                            has_images = True
                        else:
                            texts.append(t)
                text = " ".join(texts)
                if text or has_images:
                    self.chat_display.insert(tk.END, f"{self._get_user_label()}:\n", "user_label")
                    if has_images:
                        self.chat_display.insert(tk.END, "[Image] ", "image_info")
                    if text:
                        self.chat_display.insert(tk.END, text + "\n\n", "user")
                    else:
                        self.chat_display.insert(tk.END, "\n\n", "user")
            elif role == "assistant" and isinstance(content, str):
                self.chat_display.insert(tk.END, f"{self._get_friend_label()}:\n", "assistant_label")
                self.chat_display.insert(tk.END, content + "\n\n", "assistant")
            elif role == "assistant" and isinstance(content, list):
                # Extract text from list content (tool-use intermediate messages)
                texts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        t = block.get("text", "")
                        if t:
                            texts.append(t)
                    elif hasattr(block, "type") and block.type == "text":
                        t = getattr(block, "text", "")
                        if t:
                            texts.append(t)
                if texts:
                    self.chat_display.insert(tk.END, f"{self._get_friend_label()}:\n", "assistant_label")
                    self.chat_display.insert(tk.END, "".join(texts) + "\n\n", "assistant")
            # Skip intermediate assistant messages with tool_use blocks
        self.chat_display.see(tk.END)
        self.chat_display.config(state="disabled")

    # --- Image Attachment ---

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
                image_data = base64.standard_b64encode(f.read()).decode("utf-8")

            filename = os.path.basename(filepath)
            self.pending_images.append((image_data, media_type, filename))

        self.update_attach_label()

    def update_attach_label(self):
        if not self.pending_images:
            self.attach_label.config(text="")
            self.attach_label.unbind("<Button-1>")
            return
        names = ", ".join(img[2] for img in self.pending_images)
        count = len(self.pending_images)
        label = f"Attached ({count}): {names}  [click to clear]"
        self.attach_label.config(text=label)
        self.attach_label.bind("<Button-1>", lambda e: self.remove_images())

    def remove_images(self):
        self.pending_images.clear()
        self.update_attach_label()

    def on_enter_key(self, event):
        if event.state & 0x1:  # Shift held — allow newline
            return None
        if self._send_delay > 0:
            # Delay before processing to allow editing/cancellation
            self.input_field.config(state="disabled")
            self._send_timer = self.root.after(self._send_delay, self._delayed_send)
        else:
            self.send_message()
        return "break"

    def _delayed_send(self):
        self.input_field.config(state="normal")
        self.send_message()

    def send_message(self):
        if self.streaming:
            return

        user_text = self.input_field.get("1.0", "end-1c").strip()
        if not user_text and not self.pending_images:
            return

        # Intercept local slash commands
        if user_text.lower() in ("/exit", "/quit", "/bye"):
            self.input_field.delete("1.0", tk.END)
            self._new_chat()
            return

        # Capture the first message for injection
        if not self._is_second_instance and self._response_count == 0:
            self._first_message_text = user_text

        # A new message (typed here or injected by the peer — _poll_auto_msg also
        # sends through this method) lifts a model-initiated pause; a manual
        # Auto: OFF is never overridden (checked inside).
        self._resume_from_model_pause()

        # Clear input and disable send
        self.input_field.delete("1.0", tk.END)
        self.streaming = True

        # Build the message content
        images = list(self.pending_images)
        if images:
            self.pending_images.clear()
            self.update_attach_label()

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

            if user_text:
                content.append({"type": "text", "text": user_text})
            else:
                default = "What's in this image?" if len(images) == 1 else "What's in these images?"
                content.append({"type": "text", "text": default})
                user_text = default

            filenames = [img[2] for img in images]
            self.append_message("user", user_text, filenames=filenames)
        else:
            content = user_text
            self.append_message("user", user_text)

        # Add to conversation history and start streaming
        self.messages.append({"role": "user", "content": content})

        thread = threading.Thread(
            target=self.stream_worker, args=(list(self.messages),), daemon=True
        )
        thread.start()

    def _check_command_safety(self, command):
        """Check command against safety tiers. Returns (allowed, message)."""
        for pattern in COMMAND_BLOCKED:
            if re.search(pattern, command, re.IGNORECASE):
                return "blocked", f"BLOCKED: Command matches dangerous pattern ({pattern})"

        for pattern in COMMAND_CONFIRM:
            if re.search(pattern, command, re.IGNORECASE):
                if pattern in self._disabled_confirm_patterns:
                    return "skipped", pattern  # bypassed via the Safety dialog
                return "confirm", pattern
        return "safe", ""

    def _open_ps_safety_dialog(self):
        """Show the Safety dialog: one checkbox per confirm pattern (checked = confirm
        required, unchecked = bypass). Mirrors MyAgent's PS Safety dialog, with the mail
        destructive-tool sections gated by the corresponding _HAS_* flags."""
        if self._ps_safety_dialog and self._ps_safety_dialog.winfo_exists():
            self._ps_safety_dialog.lift()
            return
        dlg = tk.Toplevel(self.root)
        self._ps_safety_dialog = dlg
        dlg.withdraw()  # Hide until geometry is set to prevent flicker/repositioning
        dlg.title("Safety — Confirm Patterns")
        if IS_WINDOWS:
            dlg.transient(self.root)
        dlg.resizable(True, True)

        tk.Label(
            dlg, text="Checked items require confirmation before execution.\n"
                      "Uncheck to bypass the confirmation dialog. Command patterns\n"
                      "are matched by regex; mail entries match the tool name.",
            font=("Arial", 9), justify="left",
        ).pack(padx=15, pady=(12, 6), anchor="w")

        # A Text widget with embedded checkbuttons gives a reliably scrollable list.
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

        def _add_section(header, entries, label_fn=None):
            # checked = confirm required (pattern NOT in the disabled set); the p=entry
            # default-arg binding captures the per-iteration value (loop-closure fix).
            text_widget.insert("end", header + "\n")
            for entry in entries:
                var = tk.BooleanVar(value=entry not in self._disabled_confirm_patterns)
                cb = tk.Checkbutton(
                    text_widget, text=(label_fn(entry) if label_fn else entry),
                    variable=var, font=(MONO_FONT, 9), anchor="w",
                    bg="white", activebackground="white",
                    command=lambda p=entry, v=var: self._toggle_confirm_pattern(p, v),
                )
                text_widget.window_create("end", window=cb, stretch=True)
                text_widget.insert("end", "\n")

        shell_label = "── PowerShell command patterns ──" if IS_WINDOWS else "── Shell command patterns ──"
        _add_section(shell_label, COMMAND_CONFIRM)
        # Mail destructive-tool sections — only when the provider libs are present.
        # These bind the real tool name (proton_* even though it's shown as IMAP_*),
        # matching what the mail mixins' confirm_action checks against.
        if _HAS_MYAGENT_TOOLS and _HAS_GOOGLE and GMAIL_CONFIRM_TOOLS:
            _add_section("\n── Gmail destructive tools ──", GMAIL_CONFIRM_TOOLS)
        if _HAS_MYAGENT_TOOLS and _HAS_PROTONMAIL and PROTON_CONFIRM_TOOLS:
            _add_section("\n── IMAP mail destructive tools ──", PROTON_CONFIRM_TOOLS,
                         label_fn=lambda t: t.replace("proton_", "IMAP_", 1))
        if _HAS_MYAGENT_TOOLS and _HAS_OUTLOOK and OUTLOOK_CONFIRM_TOOLS:
            _add_section("\n── Outlook destructive tools ──", OUTLOOK_CONFIRM_TOOLS)

        text_widget.configure(state="disabled")

        def _on_close():
            self._last_ps_safety_geometry = dlg.geometry()
            self._ps_safety_dialog = None
            self._save_last_state()
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", _on_close)

        # Set geometry AFTER layout but BEFORE showing; re-apply after deiconify because
        # the embedded checkbuttons request a large natural size that overrides it on map.
        dlg.update_idletasks()
        saved_geo = getattr(self, "_last_ps_safety_geometry", None)
        if saved_geo:
            geo = self._sanitize_geometry(saved_geo)
        else:
            w, h = 560, 760
            x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
            geo = f"{w}x{h}+{x}+{y}"
        dlg.geometry(geo)
        dlg.deiconify()
        dlg.after(100, lambda: dlg.geometry(geo) if dlg.winfo_exists() else None)

    def _toggle_confirm_pattern(self, pattern, var):
        if var.get():
            self._disabled_confirm_patterns.discard(pattern)  # checked → confirm required
        else:
            self._disabled_confirm_patterns.add(pattern)      # unchecked → bypass
        self._update_ps_safety_button()
        self._save_last_state()

    def _update_ps_safety_button(self):
        n = len(self._disabled_confirm_patterns)
        label = f"Safety ({n} bypassed)" if n else "Safety"
        try:
            self.ps_safety_button.config(text=label)
        except (AttributeError, tk.TclError):
            pass  # Button doesn't exist yet

    def _request_confirmation(self, command, matched_pattern=""):
        """Request user confirmation from the main thread via a scrollable dialog. Returns True/False."""
        event = threading.Event()
        result_holder = [False]  # mutable container for the response

        def ask():
            dlg = tk.Toplevel(self.root)
            dlg.title("PowerShell — Confirm Command")
            dlg.transient(self.root)
            dlg.grab_set()
            dlg.resizable(True, True)

            row = 0
            dlg.grid_columnconfigure(0, weight=1)

            tk.Label(
                dlg, text="The following command requires your approval:",
                font=("Arial", 10), wraplength=450, justify="left",
            ).grid(row=row, column=0, sticky="w", padx=15, pady=(15, 5))
            row += 1

            # Which Safety pattern triggered this confirmation (uncheck it in Safety to bypass).
            if matched_pattern:
                tk.Label(
                    dlg, text=f"Triggered by:  {matched_pattern}",
                    font=(MONO_FONT, 9), fg="#cc3300", wraplength=450, justify="left",
                ).grid(row=row, column=0, sticky="w", padx=15, pady=(0, 5))
                row += 1

            # Scrollable text area for the command
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

            # Button bar — always visible at bottom
            btn_frame = tk.Frame(dlg)
            btn_frame.grid(row=row, column=0, pady=(0, 15))

            def on_yes():
                result_holder[0] = True
                event.set()
                dlg.destroy()

            def on_no():
                result_holder[0] = False
                event.set()
                dlg.destroy()

            tk.Button(btn_frame, text="Deny", command=on_no, width=10).pack(side=tk.LEFT, padx=10)
            tk.Button(btn_frame, text="Allow", command=on_yes, width=10).pack(side=tk.LEFT, padx=10)

            # Handle window close (X button) as denial
            dlg.protocol("WM_DELETE_WINDOW", on_no)

            # Size the dialog sensibly — cap height to 400px
            dlg.update_idletasks()
            w = max(dlg.winfo_reqwidth(), 500)
            h = min(dlg.winfo_reqheight(), 400)
            x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
            dlg.geometry(f"{w}x{h}+{x}+{y}")

        # Schedule the dialog on the main thread
        self.root.after(0, ask)
        event.wait()
        return result_holder[0]

    @staticmethod
    def _truncate_output(text, limit=20000, suffix="\n\n[Output truncated...]"):
        """Cap tool output at `limit` chars, appending a truncation notice."""
        if len(text) > limit:
            return text[:limit] + suffix
        return text

    @staticmethod
    def _kill_process_tree(proc):
        """Kill proc and every descendant. A plain proc.kill() leaves
        grandchildren alive holding the inherited stdout/stderr pipes, which
        keeps communicate() blocked forever (a nested 'powershell -File'
        spawning cmd -> cl.exe reproduced this as an indefinite agent hang)."""
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True, timeout=15, **_SUBPROCESS_NOWND,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def run_powershell(self, command, timeout=30):
        """Execute a command with safety checks (PowerShell on Windows, bash on macOS)."""
        # Tier 1 & 2 safety checks
        safety, info = self._check_command_safety(command)

        if safety == "blocked":
            return info

        if safety == "skipped":
            self.queue.put({"type": "warning", "content": f"⚠ Confirm bypassed (pattern: {info})\n"})
        elif safety == "confirm" and not self._request_confirmation(command, info):
            return "Command was rejected by the user."

        try:
            shell_cmd = (["powershell", "-NoProfile", "-Command", command]
                         if IS_WINDOWS else ["/bin/bash", "-c", command])
            # Popen, not subprocess.run: on timeout the whole process TREE must
            # die before the final pipe read, and POSIX needs its own session
            # (start_new_session) for killpg to reach every descendant.
            popen_kwargs = {} if IS_WINDOWS else {"start_new_session": True}
            proc = subprocess.Popen(
                shell_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                **popen_kwargs,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._kill_process_tree(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=10)
                except Exception:
                    stdout, stderr = "", ""
                partial = ""
                if (stdout or "").strip() or (stderr or "").strip():
                    partial = f"\nPartial output before timeout:\n{stdout or ''}"
                    if stderr:
                        partial += f"\nSTDERR:\n{stderr}"
                    partial = self._truncate_output(partial)
                return (
                    f"Error: Command timed out after {timeout} seconds and its "
                    f"process tree was killed. If the command legitimately needs "
                    f"longer, re-run it with a larger 'timeout' (max 600)." + partial
                )
            output = ""
            if stdout:
                output += stdout
            if stderr:
                output += f"\nSTDERR:\n{stderr}"
            if proc.returncode != 0:
                output += f"\n[Exit code: {proc.returncode}]"
            output = self._truncate_output(output)
            return output.strip() if output.strip() else "[No output]"
        except Exception as e:
            return f"Error running command: {e}"

    # --- CSV Search Tool ---

    def do_csv_search(self, file_path, search_value, column=None, match_mode="contains", max_results=50, delimiter=None):
        """Search a delimited text file for rows matching a value."""
        try:
            if not os.path.isfile(file_path):
                return f"Error: File not found: {file_path}"

            with open(file_path, encoding="utf-8-sig", newline="") as f:
                # Auto-detect delimiter if not specified
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

                for row_num, row in enumerate(reader, start=2):  # row 1 is header
                    cells_to_check = [row.get(column, "")] if column else row.values()

                    for cell in cells_to_check:
                        cell_lower = (cell or "").lower()
                        matched = (
                            (match_mode == "exact" and cell_lower == search_lower)
                            or (match_mode == "starts_with" and cell_lower.startswith(search_lower))
                            or (match_mode == "contains" and search_lower in cell_lower)
                        )

                        if matched:
                            matches.append((row_num, row))
                            break

                    if len(matches) >= max_results:
                        break

            if not matches:
                scope = f"in column '{column}'" if column else "in any column"
                return f"No matches found for '{search_value}' {scope}.\nColumns: {', '.join(headers)}"

            # Format results as a readable table
            lines = [f"Found {len(matches)} match(es). Columns: {', '.join(headers)}\n"]
            for row_num, row in matches:
                lines.append(f"--- Row {row_num} ---")
                lines.extend(f"  {h}: {row.get(h, '')}" for h in headers)
            if len(matches) >= max_results:
                lines.append(f"\n[Results limited to {max_results}. Use max_results to increase.]")

            return self._truncate_output("\n".join(lines))

        except UnicodeDecodeError:
            return "Error: File encoding not supported. Expected UTF-8 CSV."
        except Exception as e:
            return f"Error reading CSV: {e}"

    # --- Desktop Automation Tools ---

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
            l, t, r, b = rects[display_index]
            w, h = r - l, b - t
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
        Returns (img_w, img_h, b64_data)."""
        if region:
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            rx, ry, rw, rh = int(region[0]), int(region[1]), int(region[2]), int(region[3])
            screen_x = round(rx * scale) + ox
            screen_y = round(ry * scale) + oy
            screen_w = max(round(rw * scale), 1)
            screen_h = max(round(rh * scale), 1)
            if IS_WINDOWS:
                img = ImageGrab.grab(bbox=(screen_x, screen_y,
                                          screen_x + screen_w, screen_y + screen_h),
                                    all_screens=True)
            else:
                img = pyautogui.screenshot(region=(screen_x, screen_y, screen_w, screen_h))
            self._screenshot_offset = (screen_x, screen_y)
            phys_w_r, _ = img.size
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
            rects = self._get_windows_display_rects()
            if rects and display_idx < len(rects):
                l, t, r, b = rects[display_idx]
                img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
                self._screenshot_offset = (l, t)
            else:
                img = pyautogui.screenshot()
                self._screenshot_offset = (0, 0)
        phys_w, _ = img.size
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
        # Resize to Anthropic API image limit (1568px long edge, 1.15MP)
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
        """Capture screen (or region), resize, return as content list with image block."""
        try:
            if region:
                img_w, img_h, b64_data = self._capture_single_display(0, region=region)
                return [
                    {"type": "text", "text": f"Region screenshot ({img_w}x{img_h}). Use pixel positions from this image for mouse_click."},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}},
                ]
            rects = self._get_display_rects()
            num_displays = len(rects) if rects else 1
            if display is not None:
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
            if num_displays > 1:
                self._capture_single_display(0)  # reset scale/offset to display 0
            return result
        except Exception as e:
            return f"Screenshot error: {e}"

    def do_mouse_click(self, x, y, button="left", clicks=1):
        """Click at (x, y) with specified button and click count."""
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
        """Type text. Uses pyautogui.write for ASCII, clipboard paste for Unicode."""
        try:
            if all(ord(c) < 128 for c in text):
                pyautogui.write(text, interval=interval)
            else:
                # Clipboard paste for Unicode
                import pyperclip
                pyperclip.copy(text)
                paste_mod = "ctrl" if IS_WINDOWS else "command"
                pyautogui.hotkey(paste_mod, "v")
            return f"Typed {len(text)} characters"
        except Exception as e:
            return f"Type error: {e}"

    def do_press_key(self, keys):
        """Press a key or combination like 'ctrl+c', 'enter', 'alt+tab'."""
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
        """Scroll the mouse wheel at current position or specified (x, y)."""
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
        """Open an application by known name or full path, with optional arguments."""
        try:
            key = name.lower().strip()
            cmd = self.KNOWN_APPS.get(key, name)
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
        """Find windows matching title pattern, optionally activate the first match."""
        try:
            windows = self._find_windows_by_title(title)
            if not windows:
                return f"No windows found matching '{title}'"

            results = [f"  Title: {w['title']}\n  Position: ({w['left']}, {w['top']})\n  Size: {w['width']}x{w['height']}"
                       for w in windows]

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
        """Read text from the Windows clipboard."""
        try:
            text = self.root.clipboard_get()
            return f"Clipboard contents:\n{text}"
        except tk.TclError:
            return "Clipboard is empty or contains non-text data."
        except Exception as e:
            return f"Clipboard read error: {e}"

    def do_clipboard_write(self, text):
        """Write text to the Windows clipboard."""
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            preview = text[:100] + "..." if len(text) > 100 else text
            return f"Copied to clipboard ({len(text)} chars): {preview}"
        except Exception as e:
            return f"Clipboard write error: {e}"

    def do_wait_for_window(self, title, timeout=10):
        """Poll for a window with the given title until found or timeout."""
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
        """OCR a region of the screen. Uses winocr on Windows, Vision framework on macOS."""
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
                # Load the Vision framework for its side effect: registering the
                # VN* ObjC classes so Quartz.VNImageRequestHandler etc. resolve.
                objc.loadBundle("Vision", bundle_path="/System/Library/Frameworks/Vision.framework",
                                module_globals={})
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
        """Find a reference image on the screen."""
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
        """Drag the mouse from one point to another."""
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

    # --- Browser Automation (Playwright via CDP) ---

    def _ensure_browser(self):
        """Connect to Edge via CDP, launching it if needed. Returns the page."""
        import socket

        # If we already have a live page, check it's still usable
        if self._page is not None:
            try:
                self._page.title()
                return self._page
            except Exception:
                # Connection dropped — clean up and reconnect
                self._cleanup_browser()

        # Check if something is already listening on port 9222
        def _port_open():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                return s.connect_ex(("127.0.0.1", 9222)) == 0

        if not _port_open():
            # Try to launch Chrome or Edge with the debug port (Chrome preferred)
            if IS_WINDOWS:
                edge_paths = [
                    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
                    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
                ]
            else:
                edge_paths = [
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                ]
            edge_exe = None
            for p in edge_paths:
                if os.path.isfile(p):
                    edge_exe = p
                    break
            if not edge_exe:
                raise RuntimeError(
                    "No supported browser found. Install Microsoft Edge or Google Chrome."
                )

            import tempfile
            debug_profile = os.path.join(tempfile.gettempdir(), "selfbot_browser_debug")
            self._edge_process = subprocess.Popen(
                [edge_exe, "--remote-debugging-port=9222", "--no-first-run",
                 f"--user-data-dir={debug_profile}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Wait for the debug port to become available
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

        # Connect Playwright via CDP
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")

        # Get the first page or create one
        contexts = self._browser.contexts
        if contexts and contexts[0].pages:
            self._page = contexts[0].pages[0]
        else:
            ctx = contexts[0] if contexts else self._browser.new_context()
            self._page = ctx.new_page()

        return self._page

    def _cleanup_browser(self):
        """Disconnect Playwright. Does NOT close Edge."""
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

    def _write_inject_file(self):
        """Instance 1: write the first sent message (with user label) to the shared inject file."""
        try:
            data = {
                "label": self._get_user_label(),
                "text": self._first_message_text,
            }
            with open(INJECT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

    def _poll_inject_file(self):
        """Instance 2: poll for the inject file and load its contents into chat_display."""
        if os.path.exists(INJECT_FILE):
            try:
                with open(INJECT_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                os.remove(INJECT_FILE)
                label = data.get("label", "You")
                text = data.get("text", "")
                if text:
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, f"{label}: ", "assistant_label")
                    self.chat_display.insert(tk.END, text + "\n\n", "assistant")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
            except (OSError, json.JSONDecodeError):
                pass
        else:
            # Keep polling every 500ms until found
            self.root.after(500, self._poll_inject_file)

    def _toggle_auto_chat(self):
        """Toggle the auto-chat loop on/off."""
        on = not self._auto_chat.get()
        self._auto_chat.set(on)
        self._auto_chat_user_off = not on
        self._model_paused = False  # human took manual control either way
        if on:
            self._auto_chat_btn.config(text="Auto: ON", bg="#2e7d32", fg="white")
            self._send_delay = self._delay_seconds * 1000
        else:
            self._auto_chat_btn.config(text="Auto: OFF", bg="#c62828", fg="white")
            self._send_delay = 0
        if on and self._pending_injection and not self.streaming:
            self._pending_injection = False
            self.root.after(1000, self._inject_response_to_other)

    def do_pause_conversation(self, reason=""):
        """Let the self-chat rest — the model-callable analog of clicking Auto: OFF.

        Gives the duo an exit that isn't killing a process: auto-chat re-injects
        every completed turn into the peer forever, so before this tool the only
        way a conversation could genuinely end was taskkill. Runs on the streaming
        worker thread, so all Tk work (the _auto_chat BooleanVar, the button) is
        marshalled onto the main loop; it runs well before the turn's `complete`
        event is processed, so the reply that finishes after this call is held as
        _pending_injection instead of being delivered. The pause holds only until
        fresh traffic arrives: _resume_from_model_pause (via send_message) lifts
        it on the next human-typed or peer-injected message, and Auto: ON lifts
        it manually. _auto_chat_user_off stops _poll_for_peer instantly re-arming
        the loop, and the peer keeps running (the duo-shutdown watchdog only
        fires when a window disappears). _current_response_text is reset so a
        silent pause (model writes no closing text) can't leave the PREVIOUS
        reply latched as a pending injection — a later Auto: ON would re-send
        that stale text to the peer as a duplicate (observed live 2026-07-13).
        """
        def _pause():
            self._auto_chat.set(False)
            self._auto_chat_user_off = True
            self._model_paused = True
            self._send_delay = 0
            self._current_response_text = ""
            if not self._is_second_instance:
                self._auto_chat_btn.config(text="Auto: OFF", bg="#c62828", fg="white")
        self.root.after(0, _pause)
        note = f" — {reason.strip()}" if reason and reason.strip() else ""
        self.queue.put({
            "type": "warning",
            "content": f"⏸ Conversation paused by the model{note}. Auto-chat is OFF; "
                       "the closing reply stays in this window (a new message or "
                       "Auto: ON resumes).\n",
        })
        return (
            "Conversation paused — auto-chat is now OFF. Anything you write after "
            "this stays in your own window; it is NOT sent to your partner. Both "
            "windows remain open for the human. The conversation resumes "
            "automatically if a new message arrives (from the human or your "
            "partner), or when the human clicks Auto: ON. You may leave a short "
            "closing note or simply end your turn."
        )

    def _resume_from_model_pause(self):
        """Re-arm auto-chat when new traffic arrives after a pause_conversation.

        A new message — typed by the human OR injected by the peer (both paths
        funnel through send_message) — is an unambiguous continue signal, so a
        model-initiated pause holds only until fresh traffic arrives. Without
        this the pause was a one-way latch: instance 2 has no Auto button, so a
        model-pause there left the duo unresumable (each new message got one
        reply that was composed but never delivered back). A HUMAN's manual
        Auto: OFF (_auto_chat_user_off without _model_paused) is deliberately
        never auto-resumed. The held closing reply is dropped, not flushed:
        AUTO_MSG_FILE is single-slot, and the fresh reply is what should carry
        the conversation forward. Main thread only (like _toggle_auto_chat).
        """
        if not self._model_paused:
            return
        self._model_paused = False
        self._pending_injection = False
        self._auto_chat_user_off = False
        self._auto_chat.set(True)
        self._send_delay = self._delay_seconds * 1000
        if not self._is_second_instance:
            self._auto_chat_btn.config(text="Auto: ON", bg="#2e7d32", fg="white")
        self.queue.put({
            "type": "warning",
            "content": "▶ New message — conversation resumed (auto-chat back ON).\n",
        })

    def _on_delay_changed(self):
        """Update the send delay when the user changes the spinbox value."""
        try:
            val = int(self._delay_var.get())
            val = max(0, min(30, val))
        except (ValueError, tk.TclError):
            val = 5
        self._delay_seconds = val
        self._delay_var.set(val)
        if self._auto_chat.get():
            self._send_delay = val * 1000

    def _macos_peer_pids(self):
        """PIDs of other running SelfBot.py processes — macOS peer detection.

        Windows finds the peer by enumerating visible "Claude SelfBot" windows,
        but pygetwindow is Win32-only and reading another app's window title on
        macOS needs a Screen Recording consent. Detect by process instead: a
        SelfBot.py process IS a live window (a Tk failure kills the process, and
        a zombie's parenthesised ps command never matches). A match is a python
        executable with a SelfBot.py argument — the same shape the startup lock
        check verifies.
        """
        try:
            out = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except (OSError, subprocess.TimeoutExpired):
            return []
        peers = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 3 or not parts[0].isdigit():
                continue
            pid = int(parts[0])
            if pid == self._my_pid:
                continue
            exe = os.path.basename(parts[1]).lower()
            if exe.startswith("python") and any(p.endswith("SelfBot.py") for p in parts[2:]):
                peers.append(pid)
        return peers

    def _poll_for_peer(self):
        """Check for another SelfBot window; enable/disable auto-chat and delay accordingly.

        Also the duo-shutdown watchdog: once this instance has been paired
        (_ever_had_peer), a vanished peer means the duo is over — the survivor
        closes itself gracefully (finish streaming, save chat, exit) instead of
        lingering solo. This covers UNGRACEFUL peer deaths (crash, taskkill /F,
        a model-driven self-kill via run_command) where the dying peer never
        reached _finish_close's WM_CLOSE/SIGTERM broadcast. Three consecutive
        peer-less polls (~6 s) debounce the trigger; enumeration failures don't
        count, so a transient pygetwindow hiccup can't false-fire it.
        """
        if getattr(self, '_closing', False):
            return
        enum_ok = True
        if IS_WINDOWS and _HAS_DESKTOP:
            try:
                peers = [
                    w for w in gw.getWindowsWithTitle("Claude SelfBot")
                    if _get_window_pid(w._hWnd) != self._my_pid and w.visible
                ]
            except Exception:
                peers = []
                enum_ok = False
        elif not IS_WINDOWS:
            peers = self._macos_peer_pids()
        else:
            peers = []
        has_peer = len(peers) > 0
        was_paired = self._auto_chat.get()
        if has_peer:
            self._ever_had_peer = True
            self._peer_gone_polls = 0
        if has_peer and not was_paired and not self._auto_chat_user_off:
            # Peer just appeared — enable auto-chat and delay, show controls
            self._auto_chat.set(True)
            self._send_delay = self._delay_seconds * 1000
            if not self._is_second_instance:
                self._auto_chat_btn.config(text="Auto: ON", bg="#2e7d32", fg="white")
                self._delay_spin.pack(side=tk.RIGHT)
                self._delay_label.pack(side=tk.RIGHT, padx=(10, 5))
                self._auto_chat_btn.pack(side=tk.RIGHT)
        elif has_peer and not was_paired and not self._is_second_instance:
            # Peer present but user toggled off — just ensure controls are visible
            if not self._auto_chat_btn.winfo_ismapped():
                self._delay_spin.pack(side=tk.RIGHT)
                self._delay_label.pack(side=tk.RIGHT, padx=(10, 5))
                self._auto_chat_btn.pack(side=tk.RIGHT)
        elif not has_peer:
            # Peer gone — disable auto-chat and delay, hide all controls, reset override
            self._auto_chat.set(False)
            self._auto_chat_user_off = False
            self._send_delay = 0
            if not self._is_second_instance:
                self._auto_chat_btn.pack_forget()
                self._delay_label.pack_forget()
                self._delay_spin.pack_forget()
            # Duo-shutdown watchdog: we were paired and the peer is gone.
            if self._ever_had_peer and enum_ok:
                self._peer_gone_polls += 1
                if self._peer_gone_polls >= 3:
                    self.queue.put({"type": "warning",
                                    "content": "⚠ Peer SelfBot instance is gone — closing this instance too (duo shutdown)."})
                    self._on_close()
                    return
        self.root.after(2000, self._poll_for_peer)

    def _inject_response_to_other(self):
        """After a reply completes, write response to shared file for the other instance to pick up."""
        self._pending_injection = False
        text = self._current_response_text.strip()
        if not text:
            return
        try:
            payload = {"from_pid": self._my_pid, "text": text}
            thinking = self._current_thinking_text.strip()
            if thinking:
                payload["thinking"] = thinking
            with open(AUTO_MSG_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except OSError:
            pass

    def _poll_auto_msg(self):
        """Poll for a message file written by the other instance and send it after delay."""
        if getattr(self, '_closing', False):
            return
        if os.path.exists(AUTO_MSG_FILE):
            try:
                with open(AUTO_MSG_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                from_pid = data.get("from_pid")
                text = data.get("text", "").strip()
                if from_pid != self._my_pid and text and not self.streaming:
                    os.remove(AUTO_MSG_FILE)
                    # Display the peer's thinking block (display-only, not in conversation)
                    thinking = data.get("thinking", "").strip()
                    if thinking and self.show_thinking.get():
                        self.chat_display.config(state="normal")
                        self.chat_display.insert(tk.END, "Thinking:\n", "thinking_label")
                        self.chat_display.insert(tk.END, thinking + "\n\n", "thinking")
                        self.chat_display.see(tk.END)
                        self.chat_display.config(state="disabled")
                    self.input_field.delete("1.0", tk.END)
                    self.input_field.insert("1.0", text)
                    self.input_field.see("end")
                    self.root.update_idletasks()
                    delay = self._send_delay if self._send_delay > 0 else 0
                    if delay > 0:
                        self.input_field.config(state="disabled")
                        self.root.after(delay, self._auto_msg_delayed_send)
                    else:
                        self.send_message()
                    self.root.after(max(delay, 500), self._poll_auto_msg)
                    return
            except (OSError, json.JSONDecodeError):
                pass
        self.root.after(500, self._poll_auto_msg)

    def _auto_msg_delayed_send(self):
        """Send the auto-injected message after the configured delay."""
        self.input_field.config(state="normal")
        if getattr(self, '_closing', False):
            return
        self.send_message()

    def _auto_save_on_close(self):
        """Silently save the current chat (like pressing SAVE) before closing."""
        if not self.messages:
            return
        name = self.chat_name_entry.get().strip()
        if not name:
            # Generate a name from the first user message or use a timestamp
            for msg in self.messages:
                if msg.get("role") == "user":
                    text = msg.get("content", "")
                    if isinstance(text, list):
                        text = " ".join(
                            b.get("text", "") for b in text
                            if isinstance(b, dict) and b.get("type") == "text"
                        )
                    text = text.strip()
                    if text:
                        name = text[:50].rstrip()
                        break
            if not name:
                name = time.strftime("SelfBot_%Y%m%d_%H%M%S")
        save_name = self._save_name(name)
        self._save_chat_file(save_name, {
            "messages": self._serialize_messages(),
            "tools": self._get_tools(),
            "system_prompt": self.system_prompt,
            "system_prompt_name": self.system_prompt_name,
            "model": self.model,
            "temperature": self.temperature,
            "thinking_enabled": self.thinking_enabled,
            "thinking_effort": self.thinking_effort,
            "thinking_budget": self.thinking_budget,
        })
        # Always export the output .txt on close-save
        txt_path = os.path.join("saved_chats", self._sanitize_filename(save_name, '.txt'))
        try:
            output_text = self.chat_display.get("1.0", tk.END).rstrip()
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(output_text)
        except Exception:
            pass

    def _handle_sigterm(self, signum, frame):
        """Graceful close on SIGTERM (macOS) — the analog of Windows' WM_CLOSE.

        Received from a closing peer's duo shutdown or a plain kill/pkill. The
        handler runs between bytecodes on the main thread, so hand off to the
        Tk event loop instead of tearing down widgets from inside a signal
        frame.
        """
        if getattr(self, '_closing', False):
            return
        try:
            self.root.after(0, self._on_close)
        except tk.TclError:
            pass  # root already destroyed — process is exiting anyway

    def _on_close(self):
        """Window close handler — stop auto-chat, wait for streaming, save, close peer, destroy."""
        # Guard against re-entrant calls (peer sending WM_CLOSE back)
        if getattr(self, '_closing', False):
            return
        self._closing = True
        # Stop auto-chat immediately so no new messages get injected
        self._auto_chat.set(False)
        self._auto_chat_user_off = True
        # Remove the shared message file to prevent the peer from picking up queued messages
        try:
            os.remove(AUTO_MSG_FILE)
        except OSError:
            pass
        # If currently streaming, wait for it to finish before saving/closing
        if self.streaming:
            self.root.after(200, self._finish_close)
            return
        self._finish_close()

    def _finish_close(self):
        """Continue the close sequence once streaming has stopped."""
        # Still streaming — keep waiting
        if self.streaming:
            self.root.after(200, self._finish_close)
            return
        # Best-effort saves — a failure must never abort the close: _closing is
        # already latched, so an exception past this point leaves [X] permanently
        # dead (every later click returns at the re-entrancy guard).
        try:
            self._save_last_state()
        except Exception:
            pass
        # Auto-save the chat on close (all instances)
        try:
            self._auto_save_on_close()
        except Exception:
            pass
        # Log this process's cumulative API cost to the shared APICostLog.txt (skips if 0).
        self._log_api_cost(self._session_cost)
        # Tear down MCP servers (no-op if never connected).
        if _HAS_MYAGENT_TOOLS and _HAS_MCP:
            try:
                self._disconnect_mcp_servers()
            except Exception:
                pass
        # Find any remaining peer windows to close
        if IS_WINDOWS and _HAS_DESKTOP:
            peer_windows = []
            try:
                peer_windows = [
                    w for w in gw.getWindowsWithTitle("Claude SelfBot")
                    if _get_window_pid(w._hWnd) != self._my_pid and w.visible
                ]
            except Exception:
                pass
            # Close any other SelfBot instance — send WM_CLOSE so it shuts down cleanly
            WM_CLOSE = 0x0010
            for w in peer_windows:
                try:
                    ctypes.windll.user32.PostMessageW(w._hWnd, WM_CLOSE, 0, 0)
                except Exception:
                    # Fallback to hard kill if PostMessage fails
                    try:
                        pid = _get_window_pid(w._hWnd)
                        os.kill(pid, 9)
                    except Exception:
                        pass
        elif not IS_WINDOWS:
            # macOS analog of the WM_CLOSE broadcast: SIGTERM each peer — its
            # _handle_sigterm schedules _on_close, so it finishes any stream,
            # saves its chat, and exits cleanly (the _closing guard stops the
            # peers from bouncing the shutdown back and forth).
            for pid in self._macos_peer_pids():
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        # First instance owns the lock file — remove it on exit
        if not self._is_second_instance:
            try:
                os.remove(LOCK_FILE)
            except OSError:
                pass
            try:
                os.remove(INJECT_FILE)
            except OSError:
                pass
        try:
            os.remove(AUTO_MSG_FILE)
        except OSError:
            pass
        self._cleanup_browser()
        self.root.destroy()

    # Shared guard message for every browser tool that needs a live page.
    _NO_BROWSER = "No browser connection. Use browser_open first."

    def do_browser_open(self, url):
        try:
            self._cleanup_browser()
            if IS_WINDOWS:
                subprocess.run(
                    ["powershell", "-Command", "taskkill /F /IM msedge.exe 2>$null; Start-Sleep -Milliseconds 500"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
            if self._page is None:
                return self._NO_BROWSER
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return f"Navigated to {url} — page title: {self._page.title()}"
        except Exception as e:
            return f"Browser navigate error: {e}"

    def do_browser_click(self, selector=None, text=None):
        try:
            if self._page is None:
                return self._NO_BROWSER
            if selector:
                self._page.click(selector, timeout=10000)
                return f"Clicked element: {selector}"
            if text:
                self._page.get_by_text(text, exact=False).first.click(timeout=10000)
                return f"Clicked element with text: {text}"
            return "Provide either a 'selector' or 'text' parameter."
        except Exception as e:
            return f"Browser click error: {e}"

    def do_browser_fill(self, selector, value):
        try:
            if self._page is None:
                return self._NO_BROWSER
            self._page.fill(selector, value, timeout=10000)
            return f"Filled '{selector}' with {len(value)} characters"
        except Exception as e:
            return f"Browser fill error: {e}"

    def do_browser_get_text(self, selector=None):
        try:
            if self._page is None:
                return self._NO_BROWSER
            if selector:
                text = self._page.inner_text(selector, timeout=10000)
            else:
                text = self._page.inner_text("body", timeout=10000)
            text = self._truncate_output(text, suffix="\n\n[Content truncated at 20k chars...]")
            return text if text.strip() else "[No visible text]"
        except Exception as e:
            return f"Browser get_text error: {e}"

    def do_browser_run_js(self, code):
        try:
            if self._page is None:
                return self._NO_BROWSER
            # Wrap in a function if it uses 'return'
            if "return " in code:
                result = self._page.evaluate(f"() => {{ {code} }}")
            else:
                result = self._page.evaluate(code)
            text = json.dumps(result, indent=2, default=str) if result is not None else "[No return value]"
            return self._truncate_output(text)
        except Exception as e:
            return f"Browser JS error: {e}"

    def do_browser_screenshot(self):
        try:
            if self._page is None:
                return self._NO_BROWSER
            raw_bytes = self._page.screenshot(type="png")
            img = Image.open(io.BytesIO(raw_bytes))
            orig_w, orig_h = img.size
            max_w = 2048
            if orig_w > max_w:
                ratio = max_w / orig_w
                new_h = int(orig_h * ratio)
                img = img.resize((max_w, new_h))
                img_w, img_h = max_w, new_h
            else:
                img_w, img_h = orig_w, orig_h
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64_data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
            return [
                {"type": "text", "text": f"Browser screenshot ({img_w}x{img_h}) — page: {self._page.title()}"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}},
            ]
        except Exception as e:
            return f"Browser screenshot error: {e}"

    def do_browser_close(self):
        try:
            self._cleanup_browser()
            return "Browser connection closed. Edge remains open."
        except Exception as e:
            return f"Browser close error: {e}"

    def do_browser_wait_for(self, selector, timeout=10000):
        """Wait for an element matching a CSS selector to appear."""
        try:
            if self._page is None:
                return self._NO_BROWSER
            el = self._page.wait_for_selector(selector, timeout=timeout)
            if el is None:
                return f"Element '{selector}' not found within {timeout}ms."
            text = el.text_content() or ""
            text = text.strip()
            preview = text[:200] + "..." if len(text) > 200 else text
            return f"Element '{selector}' appeared. Text: {preview}"
        except Exception as e:
            return f"browser_wait_for error: {e}"

    def do_browser_select(self, selector, value=None, label=None):
        """Select an option in a <select> dropdown."""
        try:
            if self._page is None:
                return self._NO_BROWSER
            if value:
                self._page.select_option(selector, value=value)
                return f"Selected option with value='{value}' in '{selector}'"
            if label:
                self._page.select_option(selector, label=label)
                return f"Selected option with label='{label}' in '{selector}'"
            return "Provide either 'value' or 'label' to select an option."
        except Exception as e:
            return f"browser_select error: {e}"

    def do_browser_get_elements(self, selector, limit=10):
        """Get info about elements matching a CSS selector."""
        try:
            if self._page is None:
                return self._NO_BROWSER
            js = """
            (args) => {
                const els = document.querySelectorAll(args.selector);
                const results = [];
                const limit = args.limit;
                for (let i = 0; i < Math.min(els.length, limit); i++) {
                    const el = els[i];
                    const rect = el.getBoundingClientRect();
                    const attrs = {};
                    for (const attr of el.attributes) {
                        attrs[attr.name] = attr.value;
                    }
                    results.push({
                        index: i,
                        tag: el.tagName.toLowerCase(),
                        text: (el.textContent || '').trim().substring(0, 200),
                        attributes: attrs,
                        visible: rect.width > 0 && rect.height > 0,
                        rect: {x: Math.round(rect.x), y: Math.round(rect.y),
                               width: Math.round(rect.width), height: Math.round(rect.height)}
                    });
                }
                return {total: els.length, results: results};
            }
            """
            data = self._page.evaluate(js, {"selector": selector, "limit": limit})
            total = data.get("total", 0)
            results = data.get("results", [])
            if total == 0:
                return f"No elements found matching '{selector}'"
            lines = [f"Found {total} element(s) matching '{selector}' (showing {len(results)}):"]
            for r in results:
                attrs_str = ", ".join(f'{k}="{v}"' for k, v in r.get("attributes", {}).items())
                text_preview = r.get("text", "")[:100]
                lines.append(
                    f"  [{r['index']}] <{r['tag']}> {attrs_str}\n"
                    f"      text: {text_preview}\n"
                    f"      visible: {r['visible']}, rect: {r.get('rect', {})}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"browser_get_elements error: {e}"

    def _make_serializable(self, obj):
        """Convert SDK objects (ParsedTextBlock, ToolUseBlock, etc.) to plain dicts."""
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        return str(obj)

    @staticmethod
    def _debug_render(obj):
        """JSON-shaped rendering for the Debug payload dump, except strings
        containing newlines become indented triple-quoted blocks with REAL
        line breaks (in-file copy of StreamingMixin._debug_render — the
        system prompt with its ## Skill blocks was one endless \\n-escaped
        line). Deliberately NOT valid JSON; display only."""
        def render(o, indent):
            pad = "  " * indent
            inner = "  " * (indent + 1)
            if isinstance(o, dict):
                if not o:
                    return "{}"
                parts = [f"{inner}{json.dumps(str(k), ensure_ascii=False)}: "
                         f"{render(v, indent + 1)}" for k, v in o.items()]
                return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
            if isinstance(o, (list, tuple)):
                if not o:
                    return "[]"
                parts = [f"{inner}{render(v, indent + 1)}" for v in o]
                return "[\n" + ",\n".join(parts) + "\n" + pad + "]"
            if isinstance(o, str) and "\n" in o:
                block = "\n".join(inner + "  " + line for line in o.split("\n"))
                return '"""\n' + block + "\n" + inner + '"""'
            try:
                return json.dumps(o, ensure_ascii=False)
            except TypeError:
                return repr(o)  # display must never crash on a stray object
        return render(obj, 0)

    def _payload_for_display(self, messages):
        """Build a display-friendly copy of the payload, truncating base64 data."""
        display_msgs = []
        for msg in messages:
            content = msg.get("content", msg.get("content"))
            # Convert SDK content blocks to plain dicts
            if isinstance(content, list):
                content = [
                    self._make_serializable(block) if not isinstance(block, dict) else block
                    for block in content
                ]
            display_msgs.append({"role": msg["role"], "content": content})

        # Deep copy to avoid mutating originals when truncating
        display_msgs = copy.deepcopy(display_msgs)

        # Truncate base64 image data for readability
        def _truncate_images(blocks):
            for block in blocks:
                if isinstance(block, dict):
                    if block.get("type") == "image":
                        src = block.get("source", {})
                        if src.get("data"):
                            src["data"] = src["data"][:40] + "...[truncated]"
                    # Also handle tool_result with list content (e.g. screenshots)
                    if block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                        _truncate_images(block["content"])

        for msg in display_msgs:
            content = msg.get("content")
            if isinstance(content, list):
                _truncate_images(content)

        payload = {
            "model": self.model,
            "stream": True,
            # Cache breakpoints shown here too, for the same reason
            # _apply_thinking_params is shared: the debug preview must not drift
            # from what stream_worker actually sends.
            "system": self._cache_system(self._build_system_prompt()),
            "tools": self._get_tools(),
            "messages": self._cache_messages(display_msgs),
        }
        self._apply_thinking_params(payload)
        return self._debug_render(payload)

    def _apply_thinking_params(self, kwargs):
        """Populate max_tokens + thinking / effort / temperature on an Anthropic
        request dict per the current model's capabilities. Shared by the live
        request and the debug-payload preview so the two never drift.

        Handles the per-family quirks: Fable/Mythos 5 thinking is always on (a
        stale "off" still takes the thinking branch and is sent as plain
        adaptive); adaptive thinking asks for display="summarized" so the Show
        Thinking pane isn't blank on Fable 5 / Opus 4.7+ (their default became
        "omitted"); Sonnet 5+ "Off" is an explicit disable (omitting `thinking`
        runs adaptive there); and Opus 4.7+ / Sonnet 5+ / Fable reject
        temperature (HTTP 400), so it's skipped for them."""
        model_cap = MODEL_MAX_OUTPUT_TOKENS.get(self.model)
        always_on = self._is_always_on_thinking()
        if self.thinking_enabled or always_on:
            support = self._model_supports_thinking()
            kwargs["max_tokens"] = min(MAX_TOKENS_THINKING, model_cap) if model_cap else MAX_TOKENS_THINKING
            if support == "adaptive":
                kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
                if self.thinking_mode not in ("off", "adaptive"):
                    kwargs["output_config"] = {"effort": self.thinking_mode}
            elif support == "manual":
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
        else:
            kwargs["max_tokens"] = min(MAX_TOKENS, model_cap) if model_cap else MAX_TOKENS
            if self._thinking_on_by_default():
                kwargs["thinking"] = {"type": "disabled"}
            if not self._rejects_temperature() and self.model not in self._no_temperature:
                kwargs["temperature"] = self.temperature
        return kwargs

    @staticmethod
    def _cache_system(system_text):
        """System prompt as a single cached text block.

        Anthropic builds the cache prefix in the order tools → system →
        messages, and a breakpoint caches EVERYTHING before it — so this one
        marker also covers the whole tool array. Written once at 1.25x, read at
        0.10x on every later call.

        An empty prompt stays a bare string: a text block with "" is a 400.
        """
        if not system_text:
            return system_text
        return [{"type": "text", "text": system_text, "cache_control": dict(CACHE_CONTROL)}]

    @staticmethod
    def _cache_messages(messages, max_breakpoints=2):
        """Copy `messages` with rolling cache breakpoints on the newest turns.

        Two rolling markers (plus the static system one) stay under Anthropic's
        4-breakpoint ceiling. Call N writes a cache ending at its last message;
        call N+1 matches that prefix and reads it at 0.10x. The second, older
        marker is the safety net — if the newest write hasn't landed or has aged
        past the 5-minute TTL, the previous turn's prefix is still a hit rather
        than a full-price re-read of the entire self-chat.

        This is the single biggest cost lever in a duo run, where the history
        grows without bound (see _trim_history_for_context) and EVERY turn
        re-sends all of it. Note a trim invalidates the cache by changing the
        prefix — one re-write, then the discount resumes.

        NEVER mutates history: only the messages that receive a breakpoint are
        shallow-copied. stream_worker works on `messages` and writes it back to
        self.messages on success, and the overflow handler trims it in place —
        mutating it here would accumulate stale breakpoints past the ceiling.

        Assistant turns hold SDK block objects (stream_worker appends
        `final_message.content` verbatim), not dicts, so they are skipped; the
        eligible messages are the user turns, which are the real boundaries.
        """
        wire = list(messages)
        placed = 0
        for i in range(len(wire) - 1, -1, -1):
            if placed >= max_breakpoints:
                break
            msg = wire[i]
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                if not content:
                    continue
                blocks = [{"type": "text", "text": content,
                           "cache_control": dict(CACHE_CONTROL)}]
            elif isinstance(content, list) and content and isinstance(content[-1], dict):
                blocks = list(content)
                blocks[-1] = {**blocks[-1], "cache_control": dict(CACHE_CONTROL)}
            else:
                continue
            wire[i] = {**msg, "content": blocks}
            placed += 1
        return wire

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

    def _get_tools(self):
        """Return tool list based on which toggles are enabled."""
        tools = copy.deepcopy(TOOLS)
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
        # MyAgent-style tool subsystems (Anthropic-only). Each mail group patches
        # its `account` enum at runtime so the model only sees configured accounts.
        if self.meta_enabled.get():
            tools.extend(copy.deepcopy(SELFBOT_META_TOOLS))
        if _HAS_MYAGENT_TOOLS and _HAS_MCP and self.mcp_enabled.get() and MCP_TOOLS:
            tools.extend(copy.deepcopy(MCP_TOOLS))
        if _HAS_MYAGENT_TOOLS and _HAS_GOOGLE and self.google_enabled.get():
            google_tools = copy.deepcopy(GOOGLE_TOOLS)
            names = self._get_google_account_names()
            for t in google_tools:
                props = t.get("input_schema", {}).get("properties", {})
                if "account" in props:
                    props["account"]["enum"] = names
            tools.extend(google_tools)
        if _HAS_MYAGENT_TOOLS and _HAS_PROTONMAIL and self.proton_enabled.get():
            proton_tools = copy.deepcopy(PROTON_TOOLS)
            names = self._get_proton_account_names()
            for t in proton_tools:
                props = t.get("input_schema", {}).get("properties", {})
                if "account" in props:
                    props["account"]["enum"] = names
            tools.extend(proton_tools)
        if _HAS_MYAGENT_TOOLS and _HAS_OUTLOOK and self.outlook_enabled.get():
            outlook_tools = copy.deepcopy(OUTLOOK_TOOLS)
            names = self._get_outlook_account_names()
            for t in outlook_tools:
                props = t.get("input_schema", {}).get("properties", {})
                if "account" in props:
                    props["account"]["enum"] = names
            tools.extend(outlook_tools)
        if self.pause_enabled.get():
            tools.extend(copy.deepcopy(PAUSE_TOOLS))
        # Add get_skill tool if any on-demand skills exist
        od_names = [n for n, s in self.skills.items() if s.get("mode") == "on_demand"]
        if od_names:
            tools.append({
                "name": "get_skill",
                "description": ("Retrieve the full content of an on-demand skill by name. "
                                "Each skill's purpose is listed under '## On-Demand Skills' "
                                "in the system prompt."),
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

        Thread-safe for parallel-safe tools (csv_search, get_skill).
        Sequential tools (desktop, browser, run_command) must only be
        called from one thread.
        """
        inp = block.input
        # --- MyAgent-style subsystems (checked before the native tool chain) ---
        # MCP tools are namespaced "<server>__<tool>"; route by exact lookup.
        if _HAS_MYAGENT_TOOLS and _HAS_MCP and block.name in getattr(self, "_mcp_tools_by_name", {}):
            self._tool_info(f"MCP: {block.name}\n")
            return self.do_mcp_call(block.name, inp or {})
        # Native mail tools — dynamic dispatch to do_<name> on the mail mixins.
        if _HAS_MYAGENT_TOOLS and block.name.startswith(("gmail_", "proton_", "outlook_")):
            method = getattr(self, f"do_{block.name}", None)
            if method is None:
                return f"Unknown mail tool: {block.name}"
            self._tool_info(f"{block.name.split('_', 1)[0].capitalize()}: {block.name}\n")
            return method(inp or {})
        if block.name == "manage_skills":
            self._tool_info(f"manage_skills: {inp.get('action', '')}\n")
            return self.do_manage_skills(inp)
        if block.name == "manage_prompts":
            self._tool_info(f"manage_prompts: {inp.get('action', '')}\n")
            return self.do_manage_prompts(inp)
        if block.name == "pause_conversation":
            self._tool_info("pause_conversation\n")
            return self.do_pause_conversation((inp or {}).get("reason", ""))
        if block.name == "run_command":
            cmd = inp.get("command", "")
            try:
                cmd_timeout = int(inp.get("timeout") or 30)
            except (TypeError, ValueError):
                cmd_timeout = 30
            cmd_timeout = max(5, min(600, cmd_timeout))
            self._tool_info(f"Running: {cmd}\n")
            return self.run_powershell(cmd, timeout=cmd_timeout)
        if block.name == "csv_search":
            fp = inp.get("file_path", "")
            sv = inp.get("search_value", "")
            self._tool_info(f"Searching CSV: {os.path.basename(fp)} for '{sv}'\n")
            return self.do_csv_search(
                fp, sv,
                column=inp.get("column"),
                match_mode=inp.get("match_mode", "contains"),
                max_results=inp.get("max_results", 50),
                delimiter=inp.get("delimiter"),
            )
        if block.name in ("screenshot", "mouse_click", "type_text",
                             "press_key", "mouse_scroll", "open_application",
                             "find_window", "clipboard_read", "clipboard_write",
                             "wait_for_window", "read_screen_text",
                             "find_image_on_screen", "mouse_drag"):
            if not self.desktop_enabled.get():
                return "Desktop control is disabled. Enable the Desktop checkbox to use this tool."
            if block.name == "screenshot":
                display = inp.get("display")
                if display is not None:
                    display = int(display)  # Gemini proto returns floats
                disp_label = f"display {display}" if display is not None else "all displays"
                self._tool_info(f"Taking screenshot ({disp_label})...\n")
                region = None
                if all(k in inp for k in ("x", "y", "width", "height")):
                    region = (inp["x"], inp["y"], inp["width"], inp["height"])
                return self.do_screenshot(region, display=display)
            if block.name == "mouse_click":
                cx, cy = inp.get("x"), inp.get("y")
                if cx is None or cy is None:
                    coord = inp.get("coordinate")
                    if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                        cx, cy = coord[0], coord[1]
                    else:
                        return f"mouse_click error: missing x/y coordinates. Got: {inp}"
                self._tool_info(f"Clicking at ({cx}, {cy})...\n")
                return self.do_mouse_click(
                    cx, cy,
                    button=inp.get("button", "left"),
                    clicks=int(inp.get("clicks", 1)),
                )
            if block.name == "type_text":
                text = inp.get("text", "")
                preview = text[:50] + "..." if len(text) > 50 else text
                self._tool_info(f"Typing: {preview}\n")
                return self.do_type_text(text, interval=inp.get("interval", 0.02))
            if block.name == "press_key":
                keys = inp.get("keys", "")
                self._tool_info(f"Pressing: {keys}\n")
                return self.do_press_key(keys)
            if block.name == "mouse_scroll":
                clicks_val = int(inp.get("clicks", 0))
                self._tool_info(f"Scrolling {clicks_val} clicks...\n")
                return self.do_mouse_scroll(clicks_val, x=inp.get("x"), y=inp.get("y"))
            if block.name == "open_application":
                app_name = inp.get("name", "")
                app_args = inp.get("args")
                self._tool_info(f"Opening: {app_name}{f' {app_args}' if app_args else ''}\n")
                return self.do_open_application(app_name, args=app_args)
            if block.name == "find_window":
                title = inp.get("title", "")
                self._tool_info(f"Finding windows: {title}\n")
                return self.do_find_window(title, activate=inp.get("activate", False))
            if block.name == "clipboard_read":
                self._tool_info("Reading clipboard...\n")
                return self.do_clipboard_read()
            if block.name == "clipboard_write":
                text = inp.get("text", "")
                preview = text[:50] + "..." if len(text) > 50 else text
                self._tool_info(f"Writing to clipboard: {preview}\n")
                return self.do_clipboard_write(text)
            if block.name == "wait_for_window":
                title = inp.get("title", "")
                timeout = inp.get("timeout", 10)
                self._tool_info(f"Waiting for window: {title}\n")
                return self.do_wait_for_window(title, timeout=timeout)
            if block.name == "read_screen_text":
                rx, ry, rw, rh = inp.get("x"), inp.get("y"), inp.get("width"), inp.get("height")
                if None in (rx, ry, rw, rh):
                    return f"read_screen_text error: missing region parameters. Got: {inp}"
                self._tool_info(f"OCR region ({rx},{ry} {rw}x{rh})...\n")
                return self.do_read_screen_text(rx, ry, rw, rh)
            if block.name == "find_image_on_screen":
                path = inp.get("image_path", "")
                self._tool_info(f"Finding image: {os.path.basename(path)}\n")
                return self.do_find_image_on_screen(path, confidence=inp.get("confidence", 0.8))
            if block.name == "mouse_drag":
                sx, sy = inp.get("start_x"), inp.get("start_y")
                ex, ey = inp.get("end_x"), inp.get("end_y")
                if None in (sx, sy, ex, ey):
                    return f"mouse_drag error: missing coordinates. Got: {inp}"
                self._tool_info(f"Dragging ({sx},{sy}) to ({ex},{ey})...\n")
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
            if block.name == "browser_open":
                url = inp.get("url", "")
                self._tool_info(f"Browser: opening {url}\n")
                return self.do_browser_open(url)
            if block.name == "browser_navigate":
                url = inp.get("url", "")
                self._tool_info(f"Browser: navigating to {url}\n")
                return self.do_browser_navigate(url)
            if block.name == "browser_click":
                sel = inp.get("selector", "")
                txt = inp.get("text", "")
                target = sel or f"text='{txt}'"
                self._tool_info(f"Browser: clicking {target}\n")
                return self.do_browser_click(selector=sel or None, text=txt or None)
            if block.name == "browser_fill":
                sel = inp.get("selector", "")
                val = inp.get("value", "")
                self._tool_info(f"Browser: filling {sel}\n")
                return self.do_browser_fill(sel, val)
            if block.name == "browser_get_text":
                sel = inp.get("selector", "")
                self._tool_info(f"Browser: reading text{' from ' + sel if sel else ''}...\n")
                return self.do_browser_get_text(selector=sel or None)
            if block.name == "browser_run_js":
                code = inp.get("code", "")
                preview = code[:80] + "..." if len(code) > 80 else code
                self._tool_info(f"Browser: running JS: {preview}\n")
                return self.do_browser_run_js(code)
            if block.name == "browser_screenshot":
                self._tool_info("Browser: taking screenshot...\n")
                return self.do_browser_screenshot()
            if block.name == "browser_close":
                self._tool_info("Browser: closing connection...\n")
                return self.do_browser_close()
            if block.name == "browser_wait_for":
                sel = inp.get("selector", "")
                timeout = inp.get("timeout", 10000)
                self._tool_info(f"Browser: waiting for {sel}...\n")
                return self.do_browser_wait_for(sel, timeout=timeout)
            if block.name == "browser_select":
                sel = inp.get("selector", "")
                self._tool_info(f"Browser: selecting in {sel}...\n")
                return self.do_browser_select(sel, value=inp.get("value"), label=inp.get("label"))
            if block.name == "browser_get_elements":
                sel = inp.get("selector", "")
                limit = inp.get("limit", 10)
                self._tool_info(f"Browser: getting elements {sel}...\n")
                return self.do_browser_get_elements(sel, limit=limit)
        elif block.name == "get_skill":
            skill_name = inp.get("skill_name", "")
            self._tool_info(f"Loading skill: {skill_name}\n")
            if skill_name in self.skills and self.skills[skill_name].get("mode") == "on_demand":
                return self.skills[skill_name]["content"]
            return f"Skill not found or not on-demand: {skill_name}"
        # Catch-all: an unknown tool name, or a family branch above (desktop/
        # browser) that matched the group test but no specific handler.
        return f"Unknown tool: {block.name}"

    # --- Meta tools (SelfBot's analog of MyAgent's meta-agent tools) ---

    @staticmethod
    def _desc_length_warning(desc):
        """Soft Agent-Skills-spec guideline (<=1024 chars) — warn, never reject."""
        if len(desc) > 1024:
            return (f"\nWarning: description is {len(desc)} chars; the Agent Skills "
                    "guideline is <=1024. Consider shortening it.")
        return ""

    @staticmethod
    def _is_kebab_name(name):
        """Agent-Skills naming rule: lowercase letters/digits/hyphens, <=64
        chars. Enforced on CREATE only — an existing legacy-named skill can
        still be edited/saved (grandfathered). In-file copy of SkillsMixin's."""
        return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)) and len(name) <= 64

    @staticmethod
    def _kebabize(name):
        """Best-effort conversion of a free-typed name to kebab-case, offered
        as the suggestion when enforcement rejects. In-file copy of
        SkillsMixin's. Returns '' when nothing usable remains."""
        s = re.sub(r"[ _]+", "-", name.strip().lower())
        s = re.sub(r"[^a-z0-9-]+", "", s)
        s = re.sub(r"-{2,}", "-", s).strip("-")
        return s[:64].rstrip("-")

    def do_manage_skills(self, params):
        """CRUD the shared skills library (the skills/ SKILL.md tree). Mirrors MyAgent's manage_skills,
        including thread-safe UI refresh via _post_skill_ui_refresh."""
        action = params.get("action", "")
        name = params.get("name", "")

        if action == "list":
            if not self.skills:
                return "No skills defined."
            lines = []
            for sn, sd in sorted(self.skills.items()):
                mode = sd.get("mode", "disabled")
                desc = (sd.get("description") or "").strip()
                info = desc or sd.get("content", "")[:100].replace("\n", " ") + "..."
                lines.append(f"• {sn}  [{mode}]\n  {info}")
            return "\n".join(lines)

        if not name:
            return "Error: 'name' is required for this action."

        if action == "read":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            sd = self.skills[name]
            return json.dumps({"name": name, "content": sd.get("content", ""),
                               "mode": sd.get("mode", "disabled"),
                               "description": sd.get("description", "")}, indent=2)

        if action == "create":
            if name in self.skills:
                return f"Error: Skill '{name}' already exists. Use 'update' to modify it."
            if not self._is_kebab_name(name):
                hint = self._kebabize(name)
                suggestion = f" Try '{hint}'." if hint else ""
                return ("Error: Skill names must be Agent-Skills kebab-case — lowercase "
                        f"letters/digits/hyphens, max 64 chars (e.g. 'westpac-login').{suggestion}")
            content = params.get("content", "")
            if not content:
                return "Error: 'content' is required when creating a skill."
            mode = params.get("mode", "disabled")
            if mode not in ("disabled", "enabled", "on_demand"):
                return f"Error: Invalid mode '{mode}'. Valid modes: disabled, enabled, on_demand."
            entry = {"content": content, "mode": mode}
            desc = (params.get("description") or "").strip()
            if desc:
                entry["description"] = desc
            self.skills[name] = entry
            self._save_skills()
            self._post_skill_ui_refresh()
            return f"Skill '{name}' created successfully." + self._desc_length_warning(desc)

        if action == "update":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found. Use 'create' to add it."
            content = params.get("content")
            mode = params.get("mode")
            description = params.get("description")
            if content is None and mode is None and description is None:
                return ("Error: At least one of 'content', 'mode' or 'description' "
                        "must be provided for update.")
            if mode is not None and mode not in ("disabled", "enabled", "on_demand"):
                return f"Error: Invalid mode '{mode}'. Valid modes: disabled, enabled, on_demand."
            if content is not None:
                self.skills[name]["content"] = content
            if mode is not None:
                self.skills[name]["mode"] = mode
            desc = ""
            if description is not None:
                desc = description.strip()
                if desc:
                    self.skills[name]["description"] = desc
                else:
                    self.skills[name].pop("description", None)  # "" clears it
            self._save_skills()
            self._post_skill_ui_refresh()
            return f"Skill '{name}' updated successfully." + self._desc_length_warning(desc)

        if action == "delete":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            del self.skills[name]
            _delete_skill_tree_entry(SKILLS_DIR, name)  # _save_skills never deletes
            self._save_skills()
            self._post_skill_ui_refresh()
            return f"Skill '{name}' deleted."

        return f"Error: Unknown action '{action}'."

    # Environment-bundle keys the model may author on create/update (SelfBot's env
    # schema — Anthropic-only, so no provider/conversational; includes proton + names).
    _PROMPT_MODEL_KEYS = ("model", "temperature", "thinking_enabled", "thinking_effort",
                          "thinking_budget", "thinking_mode")
    _PROMPT_TOGGLE_KEYS = ("desktop", "browser", "meta", "mcp", "google", "proton", "outlook",
                           "pause")
    _PROMPT_NAME_KEYS = ("my_name", "my_friend")

    def do_manage_prompts(self, params):
        """CRUD SelfBot's saved system prompts (system_prompts.json) — the analog of
        MyAgent's manage_instructions, adapted to SelfBot's environment-bundle prompts.
        Disk-only; the live active prompt is untouched. THREAD-SAFE: reads only plain
        attributes (self.model / temperature / thinking_* / skills / _disabled_confirm_patterns)
        and params — never Tk widgets — so it is safe to call from the streaming worker,
        which is why create inherits from those attrs (like MyAgent) rather than from
        _capture_prompt_settings() (which reads Tk widgets on the main thread only)."""
        action = params.get("action", "")
        name = params.get("name", "")
        prompts = self._load_saved_prompts()
        if action == "list":
            return "System prompts:\n" + "\n".join(f"- {n}" for n in prompts)
        if action == "read":
            if name not in prompts:
                return f"Prompt not found: {name}"
            entry = prompts[name]
            text = self._prompt_entry_text(entry)
            out = [f"Prompt: {name}", "", text]
            if isinstance(entry, dict):
                bundle = {k: v for k, v in entry.items() if k != "text"}
                if bundle:
                    out += ["", "Bundled environment:",
                            json.dumps(bundle, indent=2, ensure_ascii=False)]
            return "\n".join(out)
        if action == "apply":
            if name not in prompts:
                return f"Prompt not found: {name}"
            entry = prompts[name]
            # _apply_prompt_settings mutates the live main screen (name entries, model /
            # thinking vars, tool-toggle BooleanVars, _on_model_selected) — all Tk, which is
            # main-thread-only. do_manage_prompts runs on the stream worker, so marshal the
            # apply onto the Tk loop and wait, mirroring _request_confirmation. Also set the
            # active prompt text/name (the env-apply alone doesn't) so the switch is complete.
            done = threading.Event()
            err = []

            def _do_apply():
                try:
                    self.system_prompt = self._prompt_entry_text(entry)
                    self.system_prompt_name = name
                    self._apply_prompt_settings(entry)
                    self._update_title()
                except Exception as exc:            # never crash the worker on a UI error
                    err.append(str(exc))
                finally:
                    done.set()

            self.root.after(0, _do_apply)
            if not done.wait(timeout=10):
                return f"apply timed out applying '{name}' to the live session."
            if err:
                return f"apply failed for '{name}': {err[0]}"
            return (f"Applied '{name}' to the LIVE session — prompt text + environment "
                    "(model, thinking, tool toggles, skills, safety) are now active.")
        if action == "create":
            if not name:
                return "create requires 'name'."
            if name in prompts:
                return f"Prompt already exists: {name} (use action='update')."
            text = params.get("text", "")
            if not text:
                return "create requires 'text'."
            # Inherit the current live environment from thread-safe plain attributes
            # (mirrors MyAgent's manage_instructions create); tool toggles come from
            # params defaulting OFF; names are bundled only if explicitly provided.
            entry = {
                "text": text,
                "model": params.get("model", self.model),
                "temperature": params.get("temperature", self.temperature),
                "thinking_enabled": params.get("thinking_enabled", self.thinking_enabled),
                "thinking_effort": params.get("thinking_effort", self.thinking_effort),
                "thinking_budget": params.get("thinking_budget", self.thinking_budget),
                "thinking_mode": params.get("thinking_mode", self.thinking_mode),
                "skill_modes": params.get(
                    "skill_modes",
                    {sn: sd.get("mode", "disabled") for sn, sd in self.skills.items()}),
                "disabled_confirm_patterns": sorted(self._disabled_confirm_patterns),
            }
            for k in self._PROMPT_TOGGLE_KEYS:
                entry[k] = bool(params.get(k, False))
            for k in self._PROMPT_NAME_KEYS:
                if k in params:
                    entry[k] = params[k]
            prompts[name] = entry
            self._save_prompts_to_disk(prompts)
            return f"Created system prompt '{name}' (bundled current environment)."
        if action == "update":
            if name not in prompts:
                return f"Prompt not found: {name}"
            entry = prompts[name]
            if not isinstance(entry, dict):
                entry = {"text": self._prompt_entry_text(entry)}
            overlay_keys = ("text", *self._PROMPT_MODEL_KEYS,
                            *self._PROMPT_TOGGLE_KEYS, *self._PROMPT_NAME_KEYS)
            provided = [k for k in (*overlay_keys, "skill_modes") if k in params]
            if not provided:
                return "update requires 'text' and/or at least one environment field."
            for k in overlay_keys:
                if k in params:
                    entry[k] = params[k]
            # skill_modes merges (only listed skills change), matching manage_instructions.
            if isinstance(params.get("skill_modes"), dict):
                merged = dict(entry.get("skill_modes", {}))
                merged.update(params["skill_modes"])
                entry["skill_modes"] = merged
            prompts[name] = entry
            self._save_prompts_to_disk(prompts)
            return f"Updated system prompt '{name}'."
        if action == "delete":
            if name not in prompts:
                return f"Prompt not found: {name}"
            if name == "Default":
                return "Cannot delete the 'Default' prompt."
            del prompts[name]
            self._save_prompts_to_disk(prompts)
            return f"Deleted system prompt '{name}'."
        return f"Unknown action: {action}"

    @staticmethod
    def _estimate_content_tokens(content):
        """Rough token estimate for one message's content: ~chars/4, images ~1600 flat.

        Used only to decide how much history to drop on a context-overflow 400 —
        never for billing — so a coarse heuristic is fine (and deliberately errs
        toward over-counting base64 images, which is the safe direction for trimming)."""
        if isinstance(content, str):
            return len(content) // 4
        if not isinstance(content, list):
            return len(str(content)) // 4
        total = 0
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "image":
                    total += 1600  # ~1MP image ≈ 1.15–1.6K tokens regardless of base64 length
                else:
                    try:
                        total += len(json.dumps(block, default=str)) // 4
                    except (TypeError, ValueError):
                        total += len(str(block)) // 4
            else:
                # Anthropic SDK block objects (assistant tool_use/text) — repr is roughly proportional
                total += len(str(block)) // 4
        return total

    def _trim_history_for_context(self, messages, reported_tokens=None, reported_max=None):
        """Drop the oldest conversation rounds so `messages` fits the context window.

        Self-chatting duos accumulate history without bound; eventually the prompt
        exceeds the model's context window and the API returns a 400 'prompt is too
        long' — which, unlike 429/529, is not retryable as-is. This trims `messages`
        in place, cutting ONLY at genuine user-turn boundaries so tool_use/tool_result
        pairs are never orphaned and the first kept message is always a real user turn.
        Always keeps at least the last two rounds. Returns the count removed (0 = could
        not trim further, i.e. even the recent context alone is over budget)."""
        def is_turn_start(m):
            # A "real" user turn (injected peer text or human input), NOT a tool_result carrier.
            if m.get("role") != "user":
                return False
            c = m.get("content")
            if isinstance(c, str):
                return True
            if isinstance(c, list):
                for b in c:
                    btype = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                    return btype != "tool_result"
                return True  # empty list — treat as a turn start
            return True
        starts = [i for i, m in enumerate(messages) if is_turn_start(m)]
        if len(starts) <= 2:
            return 0  # nothing safe to drop — keep the current exchange intact
        max_cut = starts[-2]  # never cut past this: preserve the last two rounds
        if reported_tokens and reported_max and reported_tokens > reported_max:
            target = int(reported_max * 0.75)  # headroom for system prompt, tools, and output
            need_remove = reported_tokens - target
            cut_at, removed = 0, 0
            for s in starts[1:]:
                if s > max_cut:
                    break
                removed += sum(self._estimate_content_tokens(messages[i].get("content"))
                               for i in range(cut_at, s))
                cut_at = s
                if removed >= need_remove:
                    break
        else:
            cut_at = min(starts[len(starts) // 2], max_cut)  # unknown size — drop the oldest half
        if cut_at <= 0:
            cut_at = min(starts[1], max_cut)  # guarantee at least one round is dropped
        del messages[:cut_at]
        return cut_at

    def stream_worker(self, messages):
        try:
            # Sync temperature from spinbox in case user typed a value without pressing Enter
            try:
                self.temperature = max(0.0, min(1.0, self._temp_var.get()))
            except (tk.TclError, ValueError):
                pass

            # Only emit label upfront if thinking is disabled
            label_emitted = False
            if not self.thinking_enabled:
                self.queue.put({"type": "label"})
                label_emitted = True

            call_num = 0
            while True:
                call_num += 1
                if call_num > 1:
                    self.queue.put({"type": "ensure_newline"})
                payload_text = self._payload_for_display(messages)
                self.queue.put({"type": "call_counter", "content": call_num})
                self.queue.put({"type": "debug", "content": payload_text})

                full_text = ""
                had_thinking = False
                max_retries = 10

                # Build API kwargs dynamically
                tools = self._get_tools()
                # Add Anthropic server-side tools
                tools.append({"type": "web_search_20250305", "name": "web_search"})
                tools.append({"type": "code_execution_20250825", "name": "code_execution"})
                api_kwargs = {
                    "model": self.model,
                    "system": self._cache_system(self._build_system_prompt()),
                    "messages": messages,  # replaced per attempt with the breakpointed copy
                    "tools": tools,
                }
                self._apply_thinking_params(api_kwargs)

                for attempt in range(max_retries):
                    # Rebuilt every attempt, not once above the loop: the retry
                    # paths below mutate `messages` in place (the overflow trim's
                    # `del messages[:cut_at]`), so the wire copy has to be
                    # re-derived or the retry would resend the pre-trim history —
                    # and the rolling breakpoints belong on the post-trim tail.
                    api_kwargs["messages"] = self._cache_messages(messages)
                    try:
                        with self.client.beta.messages.stream(
                                betas=["web-search-2025-03-05", "code-execution-2025-08-25", "files-api-2025-04-14"],
                                **api_kwargs) as stream:
                            in_thinking = False
                            for event in stream:
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
                                            self._tool_info("Searching the web...\n")
                                        elif tool_name == "code_execution":
                                            self._tool_info("Running code execution...\n")
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

                            final_message = stream.get_final_message()
                        # Extract code execution outputs (images, stdout) from final message
                        for blk in final_message.content:
                            if getattr(blk, "type", None) in ("code_execution_tool_result",
                                                                  "bash_code_execution_tool_result"):
                                content = getattr(blk, "content", None)
                                items = content if isinstance(content, list) else [content] if content else []
                                for item in items:
                                    itype = getattr(item, "type", None)
                                    if itype in ("code_execution_result", "bash_code_execution_result"):
                                        stdout = getattr(item, "stdout", "")
                                        if stdout:
                                            self._tool_info(stdout + "\n")
                                        for sub in getattr(item, "content", []) or []:
                                            sub_type = getattr(sub, "type", None) or ""
                                            fid = getattr(sub, "file_id", "")
                                            if fid and ("output" in sub_type or sub_type == "file"):
                                                self.queue.put({"type": "ci_image", "url": "", "file_id": fid})
                                    elif itype and ("output" in itype or itype == "file"):
                                        fid = getattr(item, "file_id", "")
                                        if fid:
                                            self.queue.put({"type": "ci_image", "url": "", "file_id": fid})
                        break  # success — exit retry loop
                    except anthropic.RateLimitError:
                        if attempt < max_retries - 1:
                            wait = min(2 ** attempt * 5, 60)  # 5s, 10s, 20s, 40s, 60s, 60s… (capped)
                            self.queue.put({
                                "type": "tool_info",
                                "content": f"Rate limited — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                            })
                            time.sleep(wait)
                            full_text = ""  # reset for retry
                        else:
                            raise  # final attempt — let outer except handle it
                    except anthropic.APIStatusError as e:
                        emsg = str(getattr(e, "message", "") or e).lower()
                        if e.status_code == 400 and "too long" in emsg:
                            # Context-window overflow — inevitable in a long self-chat, and
                            # NOT retryable as-is (unlike 429/529). Drop the oldest rounds and
                            # retry; the reported "<T> tokens > <M> maximum" sizes the trim.
                            mt = re.search(r"(\d[\d,]*)\s*tokens\s*>\s*(\d[\d,]*)", emsg)
                            rep_tok = int(mt.group(1).replace(",", "")) if mt else None
                            rep_max = int(mt.group(2).replace(",", "")) if mt else None
                            removed = self._trim_history_for_context(messages, rep_tok, rep_max)
                            if removed > 0:
                                self.queue.put({"type": "warning", "content":
                                    f"⚠ Context exceeded the model's limit — dropped the {removed} "
                                    f"oldest message(s) and retried; earlier history is no longer in "
                                    f"context.\n"})
                                full_text = ""
                                continue
                            raise  # only the last two rounds remain and still overflow — surface it
                        if (e.status_code == 400 and "temperature" in emsg
                                and "temperature" in api_kwargs):
                            # A model the version parser didn't flag rejected the
                            # sampling param — cache it, drop temperature, and retry.
                            self._no_temperature.add(self.model)
                            api_kwargs.pop("temperature", None)
                            self.queue.put({
                                "type": "tool_info",
                                "content": "Model rejected temperature — retrying without it...\n",
                            })
                            full_text = ""
                            continue
                        if e.status_code == 529 and attempt < max_retries - 1:
                            wait = min(2 ** attempt * 10, 90)  # 10s, 20s, 40s, 80s, 90s, 90s… (capped)
                            self.queue.put({
                                "type": "tool_info",
                                "content": f"API overloaded — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                            })
                            time.sleep(wait)
                            full_text = ""
                        else:
                            raise

                # --- API cost tracking (Anthropic) — accumulate this API call ---
                usage = getattr(final_message, "usage", None)
                if usage:
                    pricing = self._get_pricing(self.model)
                    if pricing:
                        ci = getattr(usage, "input_tokens", 0) or 0
                        co = getattr(usage, "output_tokens", 0) or 0
                        cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
                        cr = getattr(usage, "cache_read_input_tokens", 0) or 0
                        # input_tokens is the NON-cached count; cache-write/read are
                        # disjoint buckets, each with its own rate — no double-counting.
                        call_cost = (ci * pricing["input"] + co * pricing["output"]
                                     + cw * pricing["cache_write"] + cr * pricing["cache_read"])
                        self._session_cost += call_cost
                        self.queue.put({
                            "type": "cost_update",
                            "call_cost": call_cost,
                            "total_cost": self._session_cost,
                            "input_tokens": ci,
                            "output_tokens": co,
                            "cache_write_tokens": cw,
                            "cache_read_tokens": cr,
                        })

                if final_message.stop_reason == "tool_use":
                    # Append the full assistant message (with tool_use blocks) to history
                    messages.append({"role": "assistant", "content": final_message.content})

                    tool_blocks = [b for b in final_message.content if b.type == "tool_use"]

                    # -- Parallel-safe tools (network I/O, pure lookups) --
                    PARALLEL_SAFE = {"csv_search", "get_skill"}

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
                        if block.name in PARALLEL_SAFE:
                            parallel_items.append((idx, block))
                        else:
                            sequential_items.append((idx, block))

                    # Pre-allocate results list to preserve original order
                    tool_results_ordered = [None] * len(tool_blocks)

                    # Execute parallel-safe tools concurrently
                    if parallel_items:
                        if len(parallel_items) > 1:
                            self._tool_info(f"Running {len(parallel_items)} tools in parallel...\n")
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
                    for idx, block in sequential_items:
                        result = self._execute_tool(block)
                        tool_results_ordered[idx] = {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }

                    messages.append({"role": "user", "content": tool_results_ordered})
                    # Continue the loop — Claude will stream its response using the tool results
                else:
                    # Normal end_turn — we're done
                    break

            self.messages = messages
            self.messages.append({"role": "assistant", "content": full_text})
            self.queue.put({"type": "complete"})

        except Exception as e:
            self.queue.put({"type": "error", "content": str(e)})

    def _tool_info(self, message):
        """Post a tool_info activity line to the GUI queue.

        check_queue renders it with the grey-italic "tool_info" tag and gates it on
        the Activity (show_activity) toggle. Every tool-dispatch path calls this;
        without it, executing any tool raised AttributeError: 'App' object has no
        attribute '_tool_info'.
        """
        self.queue.put({"type": "tool_info", "content": message})

    @staticmethod
    def _get_pricing(model_name):
        """Longest-prefix Anthropic pricing lookup → a per-token dict, or None. Uses
        MyAgent's ANTHROPIC_PRICING table (per-MTok 4-tuples: input, output,
        cache_write, cache_read) so the two apps' pricing stays in sync."""
        best, best_len = None, 0
        for prefix, prices in ANTHROPIC_PRICING.items():
            if model_name.startswith(prefix) and len(prefix) > best_len:
                best, best_len = prices, len(prefix)
        if best is None:
            return None
        pt = tuple(p / 1_000_000 for p in best)
        return {"input": pt[0], "output": pt[1], "cache_write": pt[2], "cache_read": pt[3]}

    def _log_api_cost(self, total_cost):
        """Append the session's cumulative API cost to this machine's cost log
        (APICostLog_<machine>.txt in the OneDrive share, repo-root fallback — the
        SAME file MyAgent writes). Called once when the app closes. Semicolon-delimited
        {timestamp};{provider};{model};{cost} so a comma in a model name can't split a
        field; 4-decimal cost for spreadsheet summing. Skipped when the cost is zero (no
        priced usage — e.g. an unmatched model prefix). Best-effort; never raises."""
        if not total_cost or total_cost <= 0:
            return
        try:
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
            line = f"{timestamp};Anthropic;{self.model};{total_cost:.4f}\n"
            rotate_log_if_needed(APICOST_LOG_FILE, APICOST_LOG_MAX_BYTES)
            # newline="\n": the per-machine logs are read cross-platform via
            # OneDrive; Windows text-mode CRLF shows as ^M in the macOS viewer.
            with open(APICOST_LOG_FILE, "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
        except Exception:
            pass

    def _ensure_newline(self):
        """Ensure the chat display ends with a newline so the next insert starts on a fresh line."""
        end_pos = self.chat_display.index("end-1c")
        if end_pos != "1.0":
            last_char = self.chat_display.get("end-2c", "end-1c")
            if last_char != "\n":
                self.chat_display.insert(tk.END, "\n")

    def _chat_insert(self, *segments, newline_first=True, see=True):
        """Insert (text, tag) segments into the chat display in one
        enable→insert→disable cycle, starting on a fresh line by default."""
        self.chat_display.config(state="normal")
        if newline_first:
            self._ensure_newline()
        for text, tag in segments:
            self.chat_display.insert(tk.END, text, tag)
        if see:
            self.chat_display.see(tk.END)
        self.chat_display.config(state="disabled")

    def check_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg["type"] == "debug" and not self.debug_enabled.get():
                    pass  # skip payload dump when disabled
                elif msg["type"] == "call_counter" and not self.show_activity.get() and not self.debug_enabled.get() and not self.tool_calls_enabled.get():
                    pass  # skip call counter only when activity, debug, and tool calls all disabled
                elif msg["type"] == "call_counter":
                    tag = "call_counter" if self.debug_enabled.get() else "call_counter_subtle"
                    self._chat_insert((f"  Call #{msg['content']}  ", tag), ("\n", "debug"))
                elif msg["type"] == "debug":
                    self._chat_insert(
                        ("--- PAYLOAD SENT TO API ---\n", "debug_label"),
                        (msg["content"] + "\n", "debug"),
                        ("--- END PAYLOAD ---\n\n", "debug_label"),
                    )
                elif msg["type"] == "tool_call_debug" and not self.tool_calls_enabled.get():
                    pass  # skip when tool calls display disabled
                elif msg["type"] == "tool_call_debug":
                    self._chat_insert(
                        ("--- TOOL CALL ---\n", "tool_debug_label"),
                        (msg["content"] + "\n", "tool_debug"),
                        ("--- END TOOL CALL ---\n", "tool_debug_label"),
                    )
                elif msg["type"] == "thinking_start":
                    self._current_thinking_text = ""
                    if self.show_thinking.get():
                        self._chat_insert(("Thinking:\n", "thinking_label"))
                elif msg["type"] == "thinking_delta":
                    self._current_thinking_text += msg["content"]
                    if self.show_thinking.get():
                        self._chat_insert((msg["content"], "thinking"), newline_first=False)
                elif msg["type"] == "thinking_end":
                    if self.show_thinking.get():
                        self._chat_insert(("\n\n", "thinking"), newline_first=False)
                elif msg["type"] == "label":
                    self._current_response_text = ""
                    self._chat_insert((f"{self._get_friend_label()}:\n", "assistant_label"), see=False)
                elif msg["type"] == "text_delta":
                    self._current_response_text += msg["content"]
                    self._chat_insert((msg["content"], "assistant"), newline_first=False)
                elif msg["type"] == "ci_image":
                    # Code execution image — decode/download, display inline, and save
                    try:
                        url = msg.get("url", "")
                        file_id = msg.get("file_id", "")
                        img_data = None
                        if url and url.startswith("data:"):
                            parts = url.split(",", 1)
                            if len(parts) == 2:
                                img_data = base64.b64decode(parts[1])
                        elif url:
                            import urllib.request
                            with urllib.request.urlopen(url, timeout=30) as resp:
                                img_data = resp.read()
                        elif file_id and hasattr(self, 'client') and self.client:
                            resp = self.client.beta.files.download(file_id)
                            img_data = resp.read()
                        if img_data:
                            os.makedirs("saved_chats", exist_ok=True)
                            ts = time.strftime("%Y%m%d_%H%M%S")
                            img_path = os.path.join("saved_chats", f"ci_output_{ts}.png")
                            with open(img_path, "wb") as f:
                                f.write(img_data)
                            pil_img = Image.open(io.BytesIO(img_data))
                            max_w = 600
                            if pil_img.width > max_w:
                                ratio = max_w / pil_img.width
                                pil_img = pil_img.resize(
                                    (max_w, int(pil_img.height * ratio)),
                                    Image.LANCZOS,
                                )
                            from PIL import ImageTk
                            tk_img = ImageTk.PhotoImage(pil_img)
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
                        self.chat_display.insert(tk.END, f"[Code execution image error: {e}]\n", "error")
                        self.chat_display.see(tk.END)
                        self.chat_display.config(state="disabled")
                elif msg["type"] == "tool_info" and not self.show_activity.get():
                    pass  # skip tool activity when activity display disabled
                elif msg["type"] == "tool_info":
                    self._chat_insert((msg["content"], "tool_info"))
                elif msg["type"] == "warning":
                    # Always shown (e.g. confirm-bypass notices), regardless of Activity.
                    # Content already includes the ⚠ sign and a trailing newline.
                    self._chat_insert((msg["content"], "error"))
                elif msg["type"] == "cost_update" and not self.show_activity.get():
                    pass  # skip the cost line when the Activity display is off
                elif msg["type"] == "cost_update":
                    call_cost = msg["call_cost"]
                    total_cost = msg["total_cost"]
                    inp, out = msg["input_tokens"], msg["output_tokens"]
                    cw, cr = msg["cache_write_tokens"], msg["cache_read_tokens"]
                    parts = [f"in:{inp:,}  out:{out:,}"]
                    if cw:
                        parts.append(f"cache_write:{cw:,}")
                    if cr:
                        parts.append(f"cache_read:{cr:,}")
                    token_str = "  ".join(parts)
                    # Magnitude-dependent precision: sub-cent totals show 4 decimals.
                    total_str = f"{total_cost:.4f}" if total_cost < 0.01 else f"{total_cost:.2f}"
                    self._chat_insert(
                        (f"  ${call_cost:.4f} this call  |  ${total_str} session  ({token_str})\n",
                         "cost_info"))
                elif msg["type"] == "ensure_newline":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "complete":
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, "\n\n")
                    self.chat_display.config(state="disabled")
                    self.streaming = False
                    self._response_count += 1
                    # First instance: after first response, inject chat into instance 2
                    if not self._is_second_instance and self._response_count == 1:
                        self._write_inject_file()
                    # Inject response body into the other instance's input
                    if self._current_response_text.strip():
                        if self._auto_chat.get():
                            self._pending_injection = False
                            self.root.after(1000, self._inject_response_to_other)
                        else:
                            self._pending_injection = True
                    self.input_field.focus_set()
                elif msg["type"] == "error":
                    self._chat_insert((f"Error: {msg['content']}\n\n", "error"))
                    self.streaming = False
        except queue.Empty:
            pass
        self.root.after(50, self.check_queue)

    def append_message(self, role, content, filenames=None):
        self.chat_display.config(state="normal")
        if role == "user":
            self.chat_display.insert(tk.END, f"{self._get_user_label()}:\n", "user_label")
            if filenames:
                for name in filenames:
                    self.chat_display.insert(
                        tk.END, f"[Image: {name}] ", "image_info"
                    )
            self.chat_display.insert(tk.END, content + "\n\n", "user")
        self.chat_display.see(tk.END)
        self.chat_display.config(state="disabled")


if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    root = tk.Tk()
    app = App(root)
    root.mainloop()
