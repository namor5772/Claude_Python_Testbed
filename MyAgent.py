import ctypes
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
import sys
import time
import pyautogui
import pygetwindow as gw
from PIL import Image

# Desktop automation safety settings
pyautogui.FAILSAFE = True   # move mouse to (0,0) to abort
pyautogui.PAUSE = 0.3       # small delay between actions


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
        "name": "run_powershell",
        "description": (
            "Execute a PowerShell command on the local Windows PC and return its output. "
            "Use this for system tasks like listing files, checking processes, reading/writing files, "
            "getting system info, running scripts, installing software, or any other local operation. "
            "Commands run with the current user's permissions. Prefer single-line commands or "
            "semicolon-separated statements."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The PowerShell command to execute",
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
]

# Desktop automation tool definitions (pyautogui-based)
DESKTOP_TOOLS = [
    {
        "name": "screenshot",
        "description": "",  # patched at runtime with actual screen resolution
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Left edge of region to capture"},
                "y": {"type": "integer", "description": "Top edge of region to capture"},
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
            "'alt+f4', 'win+r', 'ctrl+a'. Key names follow pyautogui naming."
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
            "notepad, calculator, excel, word, powerpoint, explorer, cmd, powershell, vscode, "
            "spotify, discord, slack, teams. Or provide a full executable path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "App name (e.g. 'chrome', 'notepad') or full path to executable",
                }
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
        "description": "Read the current text contents of the Windows clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "clipboard_write",
        "description": "Write text to the Windows clipboard, replacing any current content.",
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
            "Specify the region as x, y, width, height in screen coordinates."
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
            "Open or connect to Microsoft Edge and navigate to a URL. "
            "Uses the user's real Edge profile with all cookies, logins, and extensions. "
            "If Edge isn't running, it will be launched automatically. "
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

# ── PowerShell safety guardrails ────────────────────────────────────────────

# Tier 1: Hard-blocked patterns (rejected outright, never run)
POWERSHELL_BLOCKED = [
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

# Tier 2: Confirmation-required patterns (user must approve via dialog)
POWERSHELL_CONFIRM = [
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

# ── Constants ───────────────────────────────────────────────────────────────

FALLBACK_MODELS = [
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
]
DEFAULT_MODEL = FALLBACK_MODELS[0]
MAX_TOKENS = 8192
MAX_TOKENS_THINKING = 32768
ADAPTIVE_THINKING_MODELS = {"claude-opus-4-6", "claude-sonnet-4-6"}
MANUAL_THINKING_PREFIXES = ("claude-3-5-sonnet", "claude-sonnet-4-5", "claude-haiku-4-5")
EFFORT_LEVELS = ["low", "medium", "high", "max"]
BUDGET_PRESETS = {"1K": 1024, "4K": 4096, "8K": 8192, "16K": 16384, "32K": 32768}
DEFAULT_GEOMETRY = "1050x930"

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTRUCTIONS_FILE = os.path.join(_BASE_DIR, "agent_instructions.json")
CHATS_DIR = os.path.join(_BASE_DIR, "saved_chats")
AGENT_STATE_FILE = os.path.join(_BASE_DIR, "agent_state.json")
SKILLS_FILE = os.path.join(_BASE_DIR, "skills.json")

DEFAULT_SYSTEM_PROMPT = (
    "You are an autonomous AI agent with access to a rich set of tools. "
    "Your task is given in the first user message — execute it fully and proactively.\n\n"

    "CORE TOOLS (always available):\n"
    "• web_search — search the web for current information.\n"
    "• fetch_webpage — fetch and read a specific URL.\n"
    "• run_powershell — execute PowerShell commands on the local Windows PC.\n"
    "• csv_search — search a delimited text file for records by column heading and value.\n\n"

    "DESKTOP TOOLS (available when Desktop is enabled):\n"
    "• screenshot — capture the screen. Always take a screenshot FIRST before clicking or typing.\n"
    "• mouse_click — click at specific coordinates from the screenshot.\n"
    "• type_text — type text at the current cursor position.\n"
    "• press_key — press keys or combos (e.g. 'ctrl+c', 'enter', 'alt+tab').\n"
    "• mouse_scroll — scroll up or down.\n"
    "• open_application — launch apps by name or path.\n"
    "• find_window — find and optionally activate windows by title.\n"
    "• clipboard_read / clipboard_write — read/write the Windows clipboard.\n"
    "• wait_for_window — wait until a window appears.\n"
    "• read_screen_text — OCR a screen region to extract text.\n"
    "• find_image_on_screen — find a reference image on screen.\n"
    "• mouse_drag — drag the mouse from one point to another.\n\n"

    "BROWSER TOOLS (available when Browser is enabled):\n"
    "• browser_open — connect to Edge with real profile and navigate to a URL.\n"
    "• browser_navigate — go to a new URL.\n"
    "• browser_click — click an element by CSS selector or visible text.\n"
    "• browser_fill — fill a form field by CSS selector.\n"
    "• browser_get_text — read text content from the page or element.\n"
    "• browser_run_js — execute JavaScript on the page.\n"
    "• browser_screenshot — take a screenshot of the browser page.\n"
    "• browser_close — disconnect from the browser.\n"
    "• browser_wait_for — wait for an element to appear.\n"
    "• browser_select — select an option from a dropdown.\n"
    "• browser_get_elements — get info about matching elements.\n\n"

    "GUIDELINES:\n"
    "• Execute the task autonomously — chain tools together without hesitation.\n"
    "• When multiple tools can achieve a goal, chain them together without asking.\n"
    "• For desktop automation: screenshot first, then act on what you see.\n"
    "• For browser tasks: use browser tools (not desktop tools) for precision.\n"
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


# ── Main Application ────────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Claude Agent")
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
        self.stop_requested = False
        self.pending_images = []  # list of (base64_data, media_type, filename)
        self._screenshot_scale = 1.0
        self.debug_enabled = tk.BooleanVar(value=False)
        self.tool_calls_enabled = tk.BooleanVar(value=False)
        self.show_activity = tk.BooleanVar(value=False)
        self.show_thinking = tk.BooleanVar(value=False)
        self.desktop_enabled = tk.BooleanVar(value=False)
        self.browser_enabled = tk.BooleanVar(value=False)
        self._playwright = None
        self._browser = None
        self._page = None
        self._edge_process = None
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        self.model = DEFAULT_MODEL
        self.temperature = 1.0
        self.thinking_enabled = False
        self.thinking_effort = "high"
        self.thinking_budget = 8192
        self.instruction_editor_window = None
        self.skills_editor_window = None
        self.skills = self._load_skills()
        self.available_models = self._fetch_available_models()

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

    # ── UI Setup ────────────────────────────────────────────────────────

    def setup_ui(self):
        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(1, weight=0)
        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_rowconfigure(3, weight=0)
        self.root.grid_rowconfigure(4, weight=0)
        self.root.grid_rowconfigure(5, weight=0)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=0)

        # Row 0: Model selection toolbar
        model_toolbar = tk.Frame(self.root)
        model_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 0))

        tk.Label(model_toolbar, text="Model", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
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

        # Row 1: Chat toolbar — Save + START/STOP
        chat_toolbar = tk.Frame(self.root)
        chat_toolbar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 0))

        tk.Label(chat_toolbar, text="Save Chat as", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self.chat_name_entry = tk.Entry(chat_toolbar, font=("Arial", 10), width=20)
        self.chat_name_entry.pack(side=tk.LEFT, padx=(0, 5))
        self.chat_name_entry.bind("<Return>", lambda e: self._save_chat())

        tk.Button(chat_toolbar, text="SAVE", command=self._save_chat, width=6).pack(side=tk.LEFT, padx=(0, 15))

        self._stop_button = tk.Button(
            chat_toolbar, text="STOP", command=self._stop_agent, width=8,
            bg="#c62828", fg="white", font=("Arial", 10, "bold"),
            state="disabled",
        )
        self._stop_button.pack(side=tk.RIGHT, padx=(5, 0))

        self._start_button = tk.Button(
            chat_toolbar, text="START", command=self._start_agent, width=8,
            bg="#2e7d32", fg="white", font=("Arial", 10, "bold"),
        )
        self._start_button.pack(side=tk.RIGHT, padx=(5, 0))

        # Row 2: Chat display
        self.chat_display = tk.Text(
            self.root, wrap=tk.WORD, state="disabled", font=("Arial", 11)
        )
        self.chat_display.grid(row=2, column=0, sticky="nsew", padx=(10, 0), pady=10)

        scrollbar = tk.Scrollbar(self.root, command=self.chat_display.yview)
        scrollbar.grid(row=2, column=1, sticky="ns", pady=10, padx=(0, 10))
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
            "debug", foreground="#b06000", font=("Consolas", 9)
        )
        self.chat_display.tag_config(
            "debug_label", foreground="#b06000", font=("Consolas", 9, "bold")
        )
        self.chat_display.tag_config(
            "tool_debug", foreground="#00796b", font=("Consolas", 9)
        )
        self.chat_display.tag_config(
            "tool_debug_label", foreground="#00796b", font=("Consolas", 9, "bold")
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
            font=("Consolas", 9, "italic")
        )
        self.chat_display.tag_config(
            "thinking_label", foreground="#b8860b", background="#fffde7",
            font=("Consolas", 9, "bold italic")
        )

        # Row 3: Button bar
        button_frame = tk.Frame(self.root)
        button_frame.grid(row=3, column=0, columnspan=2, pady=(0, 5))

        self.attach_button = tk.Button(
            button_frame, text="Attach Images", command=self.attach_image, width=14
        )
        self.attach_button.pack(side=tk.LEFT, padx=(0, 5))

        self.instruction_button = tk.Button(
            button_frame, text="Agent Instruction", command=self.open_instruction_editor, width=16
        )
        self.instruction_button.pack(side=tk.LEFT, padx=(5, 5))

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

        # Row 4: Checkbox row
        checkbox_frame = tk.Frame(self.root)
        checkbox_frame.grid(row=4, column=0, columnspan=2, pady=(0, 5))

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

        self.desktop_toggle = tk.Checkbutton(
            checkbox_frame, text="Desktop", variable=self.desktop_enabled,
            font=("Arial", 9),
        )
        self.desktop_toggle.pack(side=tk.LEFT, padx=(5, 0))

        self.browser_toggle = tk.Checkbutton(
            checkbox_frame, text="Browser", variable=self.browser_enabled,
            font=("Arial", 9),
        )
        self.browser_toggle.pack(side=tk.LEFT, padx=(5, 0))

        # Row 5: Attachment indicator
        self.attach_label = tk.Label(
            self.root, text="", foreground="#6a1b9a", font=("Arial", 9)
        )
        self.attach_label.grid(row=5, column=0, columnspan=2)

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

    def _on_model_selected(self, event=None):
        selected_display = self._model_var.get()
        for mid in self._model_id_list:
            if self._model_display_names.get(mid, mid) == selected_display:
                self.model = mid
                break
        support = self._model_supports_thinking()
        if support is None:
            self._thinking_var.set(False)
            self.thinking_enabled = False
            self._thinking_check.config(state="disabled")
            self._thinking_strength_combo.config(state="disabled")
            self._temp_label.config(state="normal")
            self._temp_spin.config(state="normal")
        else:
            self._thinking_check.config(state="normal")
            self._update_thinking_strength_options()
            if self.thinking_enabled:
                self._on_thinking_toggled()
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
        if mid in ADAPTIVE_THINKING_MODELS:
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

    def _update_title(self):
        if self.agent_instruction_name:
            self.root.title(f"Claude Agent — {self.agent_instruction_name}")
        else:
            self.root.title("Claude Agent")

    # ── State Persistence ───────────────────────────────────────────────

    def _save_last_state(self):
        state = {
            "last_instruction_name": self.agent_instruction_name,
            "last_model": self.model,
            "temperature": self.temperature,
            "thinking_enabled": self.thinking_enabled,
            "thinking_effort": self.thinking_effort,
            "thinking_budget": self.thinking_budget,
            "geometry": self.root.geometry(),
            "screen_width": self.root.winfo_screenwidth(),
            "screen_height": self.root.winfo_screenheight(),
        }
        with open(AGENT_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def _load_last_state(self):
        if not os.path.exists(AGENT_STATE_FILE):
            return
        try:
            with open(AGENT_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        # Restore instruction (with its images)
        instr_name = state.get("last_instruction_name", "")
        if instr_name:
            instructions = self._load_saved_instructions()
            if instr_name in instructions:
                entry = instructions[instr_name]
                self.agent_instruction = entry["text"]
                self.agent_instruction_name = instr_name
                self.pending_images = [
                    (img["data"], img["media_type"], img["filename"])
                    for img in entry.get("images", [])
                ]
                self.update_attach_label()
                self._update_title()
        # Restore model
        model = state.get("last_model", "")
        if model and model in self.available_models:
            self.model = model
            self._model_var.set(self._model_display_names.get(model, model))
        # Restore temperature
        temp = state.get("temperature")
        if temp is not None:
            self.temperature = max(0.0, min(1.0, float(temp)))
            self._temp_var.set(self.temperature)
        # Restore thinking
        self.thinking_enabled = state.get("thinking_enabled", False)
        self.thinking_effort = state.get("thinking_effort", "high")
        self.thinking_budget = state.get("thinking_budget", 8192)
        self._thinking_var.set(self.thinking_enabled)
        support = self._model_supports_thinking()
        if support is None:
            self._thinking_var.set(False)
            self.thinking_enabled = False
            self._thinking_check.config(state="disabled")
        else:
            self._thinking_check.config(state="normal")
            if support == "adaptive":
                self._thinking_strength_var.set(self.thinking_effort)
            elif support == "manual":
                for k, v in BUDGET_PRESETS.items():
                    if v == self.thinking_budget:
                        self._thinking_strength_var.set(k)
                        break
            self._on_thinking_toggled()
        # Restore geometry
        geo = state.get("geometry")
        if geo:
            sw = state.get("screen_width")
            sh = state.get("screen_height")
            if sw == self.root.winfo_screenwidth() and sh == self.root.winfo_screenheight():
                self.root.geometry(geo)

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

    def open_instruction_editor(self):
        if self.instruction_editor_window and self.instruction_editor_window.winfo_exists():
            self.instruction_editor_window.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("Agent Instruction Editor")
        win.geometry("650x500")
        win.transient(self.root)
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
        self._instr_combo.grid(row=1, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        self._instr_combo.bind("<<ComboboxSelected>>", self._on_instruction_selected)
        self._refresh_instruction_list()

        # Row 2: Text editor
        self._instr_text = tk.Text(win, wrap=tk.WORD, font=("Consolas", 10))
        self._instr_text.grid(
            row=2, column=0, columnspan=5, sticky="nsew", padx=10, pady=(5, 5)
        )
        instr_scrollbar = tk.Scrollbar(win, command=self._instr_text.yview)
        instr_scrollbar.grid(row=2, column=5, sticky="ns", pady=(5, 5), padx=(0, 5))
        self._instr_text.config(yscrollcommand=instr_scrollbar.set)

        # Row 3: Apply button
        tk.Button(
            win, text="Apply", command=self._apply_instruction,
            font=("Arial", 10, "bold"), width=16
        ).grid(row=3, column=0, columnspan=5, pady=(5, 10))

        # Grid weights
        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(2, weight=1)

        # Load current instruction into editor
        self._instr_text.insert("1.0", self.agent_instruction)
        if self.agent_instruction_name:
            self._instr_name_entry.insert(0, self.agent_instruction_name)
            self._instr_combo_var.set(self.agent_instruction_name)

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
        instructions = self._load_saved_instructions()
        instructions[name] = {
            "text": text,
            "images": [
                {"data": d, "media_type": mt, "filename": fn}
                for d, mt, fn in self.pending_images
            ],
        }
        self._save_instructions_to_disk(instructions)
        self._refresh_instruction_list()
        self._instr_combo_var.set(name)

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
        self.pending_images.clear()
        self.update_attach_label()

    def _on_instruction_selected(self, event):
        name = self._instr_combo_var.get()
        instructions = self._load_saved_instructions()
        if name in instructions:
            entry = instructions[name]
            self._instr_text.delete("1.0", tk.END)
            self._instr_text.insert("1.0", entry["text"])
            self._instr_name_entry.delete(0, tk.END)
            self._instr_name_entry.insert(0, name)
            # Load this instruction's saved images
            self.pending_images = [
                (img["data"], img["media_type"], img["filename"])
                for img in entry.get("images", [])
            ]
            self.update_attach_label()

    def _apply_instruction(self):
        text = self._instr_text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showwarning("Empty", "The instruction text is empty.", parent=self.instruction_editor_window)
            return
        self.agent_instruction = text
        self.agent_instruction_name = self._instr_name_entry.get().strip()
        # Images in pending_images are now part of this instruction
        self._update_title()
        self._save_last_state()
        self.instruction_editor_window.destroy()

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
        self.skills_button.config(text=label)

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

    def open_skills_editor(self):
        if self.skills_editor_window and self.skills_editor_window.winfo_exists():
            self.skills_editor_window.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("Skills Manager")
        win.geometry("750x500")
        win.transient(self.root)
        self.skills_editor_window = win

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

        text_editor = tk.Text(right, wrap=tk.WORD, font=("Consolas", 10))
        text_editor.grid(row=0, column=0, sticky="nsew")
        text_scrollbar = tk.Scrollbar(right, command=text_editor.yview)
        text_scrollbar.grid(row=0, column=1, sticky="ns")
        text_editor.config(yscrollcommand=text_scrollbar.set)

        win.grid_columnconfigure(0, weight=0)
        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(1, weight=1)

        refresh_list()

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
            return {"type": "tool_use", "id": block["id"], "name": block["name"], "input": block["input"]}
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
        serialized = []
        for msg in self.messages:
            content = msg["content"]
            if isinstance(content, str):
                serialized.append({"role": msg["role"], "content": content})
            elif isinstance(content, list):
                blocks = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") in ("thinking", "redacted_thinking"):
                            continue
                        blocks.append(self._clean_content_block(block))
                    elif hasattr(block, "model_dump"):
                        d = block.model_dump()
                        if d.get("type") in ("thinking", "redacted_thinking"):
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
        self._save_chat_file(name, {
            "messages": self._serialize_messages(),
            "system_prompt": self.system_prompt,
            "agent_instruction_name": self.agent_instruction_name,
            "model": self.model,
            "temperature": self.temperature,
            "thinking_enabled": self.thinking_enabled,
            "thinking_effort": self.thinking_effort,
            "thinking_budget": self.thinking_budget,
        })
        txt_path = os.path.join(CHATS_DIR, self._sanitize_filename(name, '.txt'))
        output_text = self.chat_display.get("1.0", tk.END).rstrip()
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(output_text)

    def _auto_save_on_close(self):
        if not self.messages:
            return
        name = self.chat_name_entry.get().strip()
        if not name:
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
                name = time.strftime("Agent_%Y%m%d_%H%M%S")
        self._save_chat_file(name, {
            "messages": self._serialize_messages(),
            "system_prompt": self.system_prompt,
            "agent_instruction_name": self.agent_instruction_name,
            "model": self.model,
            "temperature": self.temperature,
            "thinking_enabled": self.thinking_enabled,
            "thinking_effort": self.thinking_effort,
            "thinking_budget": self.thinking_budget,
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

        thread = threading.Thread(
            target=self.stream_worker, args=(list(self.messages),), daemon=True
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

    def _check_powershell_safety(self, command):
        for pattern in POWERSHELL_BLOCKED:
            if re.search(pattern, command, re.IGNORECASE):
                return "blocked", f"BLOCKED: Command matches dangerous pattern ({pattern})"
        for pattern in POWERSHELL_CONFIRM:
            if re.search(pattern, command, re.IGNORECASE):
                return "confirm", pattern
        return "safe", ""

    def _request_confirmation(self, command):
        event = threading.Event()
        result_holder = [False]

        def ask():
            dlg = tk.Toplevel(self.root)
            dlg.title("PowerShell — Confirm Command")
            dlg.transient(self.root)
            dlg.grab_set()
            dlg.resizable(True, True)

            dlg.grid_rowconfigure(1, weight=1)
            dlg.grid_columnconfigure(0, weight=1)

            tk.Label(
                dlg, text="The following command requires your approval:",
                font=("Arial", 10), wraplength=450, justify="left",
            ).grid(row=0, column=0, sticky="w", padx=15, pady=(15, 5))

            text_frame = tk.Frame(dlg)
            text_frame.grid(row=1, column=0, sticky="nsew", padx=15, pady=5)
            text_frame.grid_rowconfigure(0, weight=1)
            text_frame.grid_columnconfigure(0, weight=1)

            cmd_text = tk.Text(
                text_frame, wrap=tk.WORD, font=("Consolas", 10),
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
            ).grid(row=2, column=0, pady=(5, 5))

            btn_frame = tk.Frame(dlg)
            btn_frame.grid(row=3, column=0, pady=(0, 15))

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

            dlg.protocol("WM_DELETE_WINDOW", on_no)

            dlg.update_idletasks()
            w = max(dlg.winfo_reqwidth(), 500)
            h = min(dlg.winfo_reqheight(), 400)
            x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
            y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
            dlg.geometry(f"{w}x{h}+{x}+{y}")

        self.root.after(0, ask)
        event.wait()
        return result_holder[0]

    def run_powershell(self, command):
        safety, info = self._check_powershell_safety(command)
        if safety == "blocked":
            return info
        if safety == "confirm":
            if not self._request_confirmation(command):
                return "Command was rejected by the user."
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=30,
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
    }

    def do_screenshot(self, region=None):
        try:
            if region:
                img = pyautogui.screenshot(region=region)
            else:
                img = pyautogui.screenshot()
            orig_w, orig_h = img.size
            max_w = 1280
            if orig_w > max_w:
                ratio = orig_w / max_w
                new_h = int(orig_h / ratio)
                img = img.resize((max_w, new_h))
                self._screenshot_scale = ratio
                img_w, img_h = max_w, new_h
            else:
                self._screenshot_scale = 1.0
                img_w, img_h = orig_w, orig_h
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64_data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
            return [
                {"type": "text", "text": f"Screenshot captured ({img_w}x{img_h}). Click coordinates are automatically mapped to the screen — just use the pixel positions you see in this image."},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}},
            ]
        except Exception as e:
            return f"Screenshot error: {e}"

    def do_mouse_click(self, x, y, button="left", clicks=1):
        try:
            scale = self._screenshot_scale
            screen_x = int(x * scale)
            screen_y = int(y * scale)
            pyautogui.click(screen_x, screen_y, button=button, clicks=clicks)
            return f"Clicked ({button}, {clicks}x) at screen ({screen_x}, {screen_y}) [image coords ({x}, {y}), scale {scale:.2f}x]"
        except Exception as e:
            return f"Click error: {e}"

    def do_type_text(self, text, interval=0.02):
        try:
            if all(ord(c) < 128 for c in text):
                pyautogui.write(text, interval=interval)
            else:
                import pyperclip
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
            return f"Typed {len(text)} characters"
        except Exception as e:
            return f"Type error: {e}"

    def do_press_key(self, keys):
        try:
            parts = [k.strip().lower() for k in keys.split("+")]
            aliases = {"windows": "win", "control": "ctrl", "return": "enter", "esc": "escape"}
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
            kwargs = {}
            if x is not None:
                kwargs["x"] = int(x * scale)
            if y is not None:
                kwargs["y"] = int(y * scale)
            pyautogui.scroll(clicks, **kwargs)
            direction = "up" if clicks > 0 else "down"
            pos = f" at ({x}, {y})" if x is not None else ""
            return f"Scrolled {direction} {abs(clicks)} clicks{pos}"
        except Exception as e:
            return f"Scroll error: {e}"

    def do_open_application(self, name):
        try:
            key = name.lower().strip()
            if key in self.KNOWN_APPS:
                cmd = self.KNOWN_APPS[key]
                subprocess.Popen(cmd, shell=True)
                return f"Opened {name} (command: {cmd})"
            else:
                subprocess.Popen(name, shell=True)
                return f"Launched: {name}"
        except Exception as e:
            return f"Error opening {name}: {e}"

    def do_find_window(self, title, activate=False):
        try:
            windows = gw.getWindowsWithTitle(title)
            if not windows:
                return f"No windows found matching '{title}'"
            results = []
            for w in windows:
                results.append(f"  Title: {w.title}\n  Position: ({w.left}, {w.top})\n  Size: {w.width}x{w.height}")
            if activate and windows:
                try:
                    win = windows[0]
                    if win.isMinimized:
                        win.restore()
                    win.activate()
                    results.insert(0, f"Activated: {win.title}")
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
                windows = gw.getWindowsWithTitle(title)
                if windows:
                    w = windows[0]
                    return (
                        f"Window found: {w.title}\n"
                        f"Position: ({w.left}, {w.top})\n"
                        f"Size: {w.width}x{w.height}"
                    )
                time.sleep(0.5)
            return f"Timed out after {timeout}s waiting for window '{title}'"
        except Exception as e:
            return f"Wait for window error: {e}"

    def do_read_screen_text(self, x, y, width, height):
        try:
            import winocr
            import asyncio
            scale = self._screenshot_scale
            sx = int(x * scale)
            sy = int(y * scale)
            sw = int(width * scale)
            sh = int(height * scale)
            img = pyautogui.screenshot(region=(sx, sy, sw, sh))
            result = asyncio.run(winocr.recognize_pil(img, lang="en"))
            text = result.text.strip()
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
            img_cx = int(cx / scale) if scale else cx
            img_cy = int(cy / scale) if scale else cy
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
            sx = int(start_x * scale)
            sy = int(start_y * scale)
            ex = int(end_x * scale)
            ey = int(end_y * scale)
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
        import socket

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
            edge_paths = [
                os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
            ]
            edge_exe = None
            for p in edge_paths:
                if os.path.isfile(p):
                    edge_exe = p
                    break
            if not edge_exe:
                raise RuntimeError("Microsoft Edge not found. Install Edge or check its path.")
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
            max_w = 1280
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
            content = msg.get("content", msg.get("content"))
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

        payload = {
            "model": self.model,
            "stream": True,
            "system": self._build_system_prompt(),
            "tools": self._get_tools(),
            "messages": display_msgs,
        }
        if self.thinking_enabled:
            support = self._model_supports_thinking()
            payload["max_tokens"] = MAX_TOKENS_THINKING
            if support == "adaptive":
                payload["thinking"] = {"type": "adaptive"}
                payload["output_config"] = {"effort": self.thinking_effort}
            elif support == "manual":
                payload["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
        else:
            payload["max_tokens"] = MAX_TOKENS
            payload["temperature"] = self.temperature
        return json.dumps(payload, indent=2)

    def _get_tools(self):
        tools = copy.deepcopy(TOOLS)
        if self.desktop_enabled.get():
            desktop = copy.deepcopy(DESKTOP_TOOLS)
            screen_w, screen_h = pyautogui.size()
            for tool in desktop:
                if tool["name"] == "screenshot":
                    tool["description"] = (
                        f"Take a screenshot of the screen (resolution {screen_w}x{screen_h}). "
                        "Always use this FIRST to see what is on the screen before clicking or typing. "
                        "The image may be resized. For mouse_click, use the pixel coordinates as you see "
                        "them in the image — they are automatically scaled to screen coordinates. "
                        "Optionally capture only a region by specifying x, y, width, height."
                    )
                    break
            tools.extend(desktop)
        if self.browser_enabled.get():
            tools.extend(copy.deepcopy(BROWSER_TOOLS))
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
                payload_text = self._payload_for_display(messages)
                self.queue.put({"type": "call_counter", "content": call_num})
                self.queue.put({"type": "debug", "content": payload_text})

                full_text = ""
                had_thinking = False
                max_retries = 10

                api_kwargs = {
                    "model": self.model,
                    "system": self._build_system_prompt(),
                    "messages": messages,
                    "tools": self._get_tools(),
                }
                if self.thinking_enabled:
                    support = self._model_supports_thinking()
                    api_kwargs["max_tokens"] = MAX_TOKENS_THINKING
                    if support == "adaptive":
                        api_kwargs["thinking"] = {"type": "adaptive"}
                        api_kwargs["output_config"] = {"effort": self.thinking_effort}
                    elif support == "manual":
                        api_kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
                else:
                    api_kwargs["max_tokens"] = MAX_TOKENS
                    api_kwargs["temperature"] = self.temperature

                for attempt in range(max_retries):
                    try:
                        with self.client.messages.stream(**api_kwargs) as stream:
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

                if self.stop_requested:
                    self.queue.put({"type": "tool_info", "content": "Agent stopped by user.\n"})
                    break

                if final_message.stop_reason == "tool_use":
                    messages.append({"role": "assistant", "content": final_message.content})

                    tool_results = []
                    for block in final_message.content:
                        if block.type == "tool_use":
                            tool_call_detail = json.dumps(
                                {"tool": block.name, "id": block.id, "input": block.input},
                                indent=2,
                            )
                            self.queue.put({"type": "tool_call_debug", "content": tool_call_detail})

                            if block.name == "web_search":
                                query = block.input.get("query", "")
                                self.queue.put({"type": "tool_info", "content": f"Searching: {query}\n"})
                                result = self.search_web(query)
                            elif block.name == "fetch_webpage":
                                url = block.input.get("url", "")
                                self.queue.put({"type": "tool_info", "content": f"Fetching: {url}\n"})
                                result = self.fetch_url(url)
                            elif block.name == "run_powershell":
                                cmd = block.input.get("command", "")
                                self.queue.put({"type": "tool_info", "content": f"Running: {cmd}\n"})
                                result = self.run_powershell(cmd)
                            elif block.name == "csv_search":
                                inp = block.input
                                fp = inp.get("file_path", "")
                                sv = inp.get("search_value", "")
                                self.queue.put({"type": "tool_info", "content": f"Searching CSV: {os.path.basename(fp)} for '{sv}'\n"})
                                result = self.do_csv_search(
                                    fp, sv,
                                    column=inp.get("column"),
                                    match_mode=inp.get("match_mode", "contains"),
                                    max_results=inp.get("max_results", 50),
                                    delimiter=inp.get("delimiter"),
                                )
                            elif block.name in ("screenshot", "mouse_click", "type_text",
                                                 "press_key", "mouse_scroll", "open_application",
                                                 "find_window", "clipboard_read", "clipboard_write",
                                                 "wait_for_window", "read_screen_text",
                                                 "find_image_on_screen", "mouse_drag"):
                                if not self.desktop_enabled.get():
                                    result = "Desktop control is disabled. Enable the Desktop checkbox to use this tool."
                                else:
                                    inp = block.input
                                    if block.name == "screenshot":
                                        self.queue.put({"type": "tool_info", "content": "Taking screenshot...\n"})
                                        region = None
                                        if all(k in inp for k in ("x", "y", "width", "height")):
                                            region = (inp["x"], inp["y"], inp["width"], inp["height"])
                                        result = self.do_screenshot(region)
                                    elif block.name == "mouse_click":
                                        self.queue.put({"type": "tool_info", "content": f"Clicking at ({inp.get('x')}, {inp.get('y')})...\n"})
                                        result = self.do_mouse_click(
                                            inp["x"], inp["y"],
                                            button=inp.get("button", "left"),
                                            clicks=inp.get("clicks", 1),
                                        )
                                    elif block.name == "type_text":
                                        text = inp.get("text", "")
                                        preview = text[:50] + "..." if len(text) > 50 else text
                                        self.queue.put({"type": "tool_info", "content": f"Typing: {preview}\n"})
                                        result = self.do_type_text(text, interval=inp.get("interval", 0.02))
                                    elif block.name == "press_key":
                                        keys = inp.get("keys", "")
                                        self.queue.put({"type": "tool_info", "content": f"Pressing: {keys}\n"})
                                        result = self.do_press_key(keys)
                                    elif block.name == "mouse_scroll":
                                        clicks_val = inp.get("clicks", 0)
                                        self.queue.put({"type": "tool_info", "content": f"Scrolling {clicks_val} clicks...\n"})
                                        result = self.do_mouse_scroll(clicks_val, x=inp.get("x"), y=inp.get("y"))
                                    elif block.name == "open_application":
                                        app_name = inp.get("name", "")
                                        self.queue.put({"type": "tool_info", "content": f"Opening: {app_name}\n"})
                                        result = self.do_open_application(app_name)
                                    elif block.name == "find_window":
                                        title = inp.get("title", "")
                                        self.queue.put({"type": "tool_info", "content": f"Finding windows: {title}\n"})
                                        result = self.do_find_window(title, activate=inp.get("activate", False))
                                    elif block.name == "clipboard_read":
                                        self.queue.put({"type": "tool_info", "content": "Reading clipboard...\n"})
                                        result = self.do_clipboard_read()
                                    elif block.name == "clipboard_write":
                                        text = inp.get("text", "")
                                        preview = text[:50] + "..." if len(text) > 50 else text
                                        self.queue.put({"type": "tool_info", "content": f"Writing to clipboard: {preview}\n"})
                                        result = self.do_clipboard_write(text)
                                    elif block.name == "wait_for_window":
                                        title = inp.get("title", "")
                                        timeout = inp.get("timeout", 10)
                                        self.queue.put({"type": "tool_info", "content": f"Waiting for window: {title}\n"})
                                        result = self.do_wait_for_window(title, timeout=timeout)
                                    elif block.name == "read_screen_text":
                                        self.queue.put({"type": "tool_info", "content": f"OCR region ({inp.get('x')},{inp.get('y')} {inp.get('width')}x{inp.get('height')})...\n"})
                                        result = self.do_read_screen_text(inp["x"], inp["y"], inp["width"], inp["height"])
                                    elif block.name == "find_image_on_screen":
                                        path = inp.get("image_path", "")
                                        self.queue.put({"type": "tool_info", "content": f"Finding image: {os.path.basename(path)}\n"})
                                        result = self.do_find_image_on_screen(path, confidence=inp.get("confidence", 0.8))
                                    elif block.name == "mouse_drag":
                                        self.queue.put({"type": "tool_info", "content": f"Dragging ({inp.get('start_x')},{inp.get('start_y')}) to ({inp.get('end_x')},{inp.get('end_y')})...\n"})
                                        result = self.do_mouse_drag(
                                            inp["start_x"], inp["start_y"],
                                            inp["end_x"], inp["end_y"],
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
                                    result = "Browser tools are disabled. Enable the Browser checkbox to use this tool."
                                else:
                                    inp = block.input
                                    if block.name == "browser_open":
                                        url = inp.get("url", "")
                                        self.queue.put({"type": "tool_info", "content": f"Browser: opening {url}\n"})
                                        result = self.do_browser_open(url)
                                    elif block.name == "browser_navigate":
                                        url = inp.get("url", "")
                                        self.queue.put({"type": "tool_info", "content": f"Browser: navigating to {url}\n"})
                                        result = self.do_browser_navigate(url)
                                    elif block.name == "browser_click":
                                        sel = inp.get("selector", "")
                                        txt = inp.get("text", "")
                                        target = sel or f"text='{txt}'"
                                        self.queue.put({"type": "tool_info", "content": f"Browser: clicking {target}\n"})
                                        result = self.do_browser_click(selector=sel or None, text=txt or None)
                                    elif block.name == "browser_fill":
                                        sel = inp.get("selector", "")
                                        val = inp.get("value", "")
                                        self.queue.put({"type": "tool_info", "content": f"Browser: filling {sel}\n"})
                                        result = self.do_browser_fill(sel, val)
                                    elif block.name == "browser_get_text":
                                        sel = inp.get("selector", "")
                                        self.queue.put({"type": "tool_info", "content": f"Browser: reading text{' from ' + sel if sel else ''}...\n"})
                                        result = self.do_browser_get_text(selector=sel or None)
                                    elif block.name == "browser_run_js":
                                        code = inp.get("code", "")
                                        preview = code[:80] + "..." if len(code) > 80 else code
                                        self.queue.put({"type": "tool_info", "content": f"Browser: running JS: {preview}\n"})
                                        result = self.do_browser_run_js(code)
                                    elif block.name == "browser_screenshot":
                                        self.queue.put({"type": "tool_info", "content": "Browser: taking screenshot...\n"})
                                        result = self.do_browser_screenshot()
                                    elif block.name == "browser_close":
                                        self.queue.put({"type": "tool_info", "content": "Browser: closing connection...\n"})
                                        result = self.do_browser_close()
                                    elif block.name == "browser_wait_for":
                                        sel = inp.get("selector", "")
                                        timeout = inp.get("timeout", 10000)
                                        self.queue.put({"type": "tool_info", "content": f"Browser: waiting for {sel}...\n"})
                                        result = self.do_browser_wait_for(sel, timeout=timeout)
                                    elif block.name == "browser_select":
                                        sel = inp.get("selector", "")
                                        self.queue.put({"type": "tool_info", "content": f"Browser: selecting in {sel}...\n"})
                                        result = self.do_browser_select(sel, value=inp.get("value"), label=inp.get("label"))
                                    elif block.name == "browser_get_elements":
                                        sel = inp.get("selector", "")
                                        limit = inp.get("limit", 10)
                                        self.queue.put({"type": "tool_info", "content": f"Browser: getting elements {sel}...\n"})
                                        result = self.do_browser_get_elements(sel, limit=limit)
                            elif block.name == "get_skill":
                                skill_name = block.input.get("skill_name", "")
                                self.queue.put({"type": "tool_info", "content": f"Loading skill: {skill_name}\n"})
                                if skill_name in self.skills and self.skills[skill_name].get("mode") == "on_demand":
                                    result = self.skills[skill_name]["content"]
                                else:
                                    result = f"Skill not found or not on-demand: {skill_name}"
                            else:
                                result = f"Unknown tool: {block.name}"

                            if isinstance(result, list):
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result,
                                })
                            else:
                                tool_results.append({
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result,
                                })

                    messages.append({"role": "user", "content": tool_results})
                else:
                    # Normal end_turn — agent is done
                    break

            self.messages = messages
            self.messages.append({"role": "assistant", "content": full_text})
            self.queue.put({"type": "complete"})

        except Exception as e:
            self.queue.put({"type": "error", "content": str(e)})

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
                    self.chat_display.insert(tk.END, f"  Call #{msg['content']}  ", tag)
                    self.chat_display.insert(tk.END, "\n", "debug")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "debug":
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, "--- PAYLOAD SENT TO API ---\n", "debug_label")
                    self.chat_display.insert(tk.END, msg["content"] + "\n", "debug")
                    self.chat_display.insert(tk.END, "--- END PAYLOAD ---\n\n", "debug_label")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "tool_call_debug" and not self.tool_calls_enabled.get():
                    pass
                elif msg["type"] == "tool_call_debug":
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, "--- TOOL CALL ---\n", "tool_debug_label")
                    self.chat_display.insert(tk.END, msg["content"] + "\n", "tool_debug")
                    self.chat_display.insert(tk.END, "--- END TOOL CALL ---\n", "tool_debug_label")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "thinking_start":
                    self._current_thinking_text = ""
                    if self.show_thinking.get():
                        self.chat_display.config(state="normal")
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
                    self.chat_display.insert(tk.END, "Agent:\n", "assistant_label")
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "text_delta":
                    self._current_response_text += msg["content"]
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, msg["content"], "assistant")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "tool_info" and not self.show_activity.get():
                    pass
                elif msg["type"] == "tool_info":
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, msg["content"], "tool_info")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "complete":
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, "\n\n")
                    self.chat_display.config(state="disabled")
                    self.streaming = False
                    self._start_button.config(state="normal")
                    self._stop_button.config(state="disabled")
                elif msg["type"] == "error":
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(
                        tk.END, f"Error: {msg['content']}\n\n", "error"
                    )
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                    self.streaming = False
                    self._start_button.config(state="normal")
                    self._stop_button.config(state="disabled")
        except queue.Empty:
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
        self.root.destroy()


if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    root = tk.Tk()
    app = App(root)
    root.mainloop()
