import sys
IS_WINDOWS = sys.platform == "win32"

import ctypes
if IS_WINDOWS:
    import ctypes.wintypes
    # Fix DPI scaling for desktop automation tools — must run before any window creation.
    # Without this, Windows display scaling (125%, 150%, etc.) causes screenshot pixel
    # coordinates and mouse click coordinates to use different scales, so clicks miss.
    # MyAgent's bootstrap since 2026-10-14 (the Desktop tools are its DesktopMixin
    # now): PER_MONITOR_AWARE_V2 (-4) over v1, which reported a secondary monitor
    # at another scale as a logically-scaled smaller rect — a low-res capture and
    # clicks landing in the wrong place there; v1, then the legacy call, as fallbacks.
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4)  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        )
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # v1 fallback
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

import tkinter as tk
from tkinter import font as tkfont, messagebox, filedialog, ttk
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
import tempfile
from datetime import datetime

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
        ANTHROPIC_PRICING, ANTHROPIC_WEB_SEARCH_FEE, APICOST_LOG_MAX_BYTES,
        ANTHROPIC_THINKING_BINDING_BETA, ANTHROPIC_THINKING_BLOCK_BINDING,
        ANTHROPIC_SERVER_FALLBACK_BETA, ANTHROPIC_SERVER_FALLBACKS,
        ANTHROPIC_MAX_OUTPUT_TOKENS, ANTHROPIC_MAX_OUTPUT_DEFAULT,
        ANTHROPIC_LONG_CONTEXT_PRICING,
        TOOLBAR_ACTIVE_BG, LIST_TITLE_BG,
        _HAS_MCP, _HAS_GOOGLE, _HAS_PROTONMAIL, _HAS_OUTLOOK,
        # Tool parity with MyAgent (2026-10-14): the schemas and flags of the
        # tool sets SelfBot inherits by mixin below — see CLAUDE_SELFBOT.md.
        TOOLS as _MYAGENT_TOOLS, FILE_TOOLS, DESKTOP_TOOLS, BROWSER_TOOLS,
        EXCEL_TOOLS, PHYSICAL_TOOLS, PARALLEL_SAFE_TOOLS,
        _HAS_EXCEL, _HAS_CAMERA, _HAS_MICROPHONE, _HAS_PHYSICAL,
    )
    from myagent.helpers import rotate_log_if_needed, parse_output_cap, strip_old_images, input_wait_timer
    from myagent.retry_util import connection_error_backoff
    from myagent.mcp_mixin import MCPMixin
    from myagent.gmail_mixin import GmailMixin
    from myagent.protonmail_mixin import ProtonMailMixin
    from myagent.outlook_mixin import OutlookMixin
    from myagent.document_mixin import DocumentMixin
    from myagent.file_mixin import FileMixin
    from myagent.desktop_mixin import DesktopMixin
    from myagent.browser_mixin import BrowserMixin
    from myagent.excel_mixin import ExcelMixin
    from myagent.physical_mixin import PhysicalMixin
    from myagent.voice_mixin import VoiceMixin
    _HAS_MYAGENT_TOOLS = True
except Exception:
    _HAS_MYAGENT_TOOLS = False
    _HAS_MCP = _HAS_GOOGLE = _HAS_PROTONMAIL = _HAS_OUTLOOK = False
    _HAS_EXCEL = _HAS_CAMERA = _HAS_MICROPHONE = _HAS_PHYSICAL = False
    GOOGLE_TOOLS = PROTON_TOOLS = OUTLOOK_TOOLS = MCP_TOOLS = []
    _MYAGENT_TOOLS = FILE_TOOLS = DESKTOP_TOOLS = BROWSER_TOOLS = EXCEL_TOOLS = PHYSICAL_TOOLS = []
    PARALLEL_SAFE_TOOLS = set()
    GMAIL_CONFIRM_TOOLS = PROTON_CONFIRM_TOOLS = OUTLOOK_CONFIRM_TOOLS = []
    ANTHROPIC_PRICING = {}
    ANTHROPIC_WEB_SEARCH_FEE = 10.00 / 1000   # USD per executed web search
    APICOST_LOG_MAX_BYTES = 100_000
    # Claude Fable 5.1 beta surfaces (see myagent/constants.py for the why):
    # preserved-thinking drop_block binding + server-side refusal fallbacks.
    ANTHROPIC_THINKING_BINDING_BETA = "thinking-binding-controls-2026-08-01"
    ANTHROPIC_THINKING_BLOCK_BINDING = {"prefix_mismatch_behavior": "drop_block"}
    ANTHROPIC_SERVER_FALLBACK_BETA = "server-side-fallback-2026-07-01"
    ANTHROPIC_SERVER_FALLBACKS = "default"
    # The Anthropic output ceiling (MyAgent 2026-10-11, here 2026-10-14) and
    # Haiku 5.5's second rate card (2026-10-08) — see myagent/constants.py.
    # Without the package every model takes the default ceiling (a 400
    # teaches a lower one) and no model has a second card.
    ANTHROPIC_MAX_OUTPUT_TOKENS = ()
    ANTHROPIC_MAX_OUTPUT_DEFAULT = 128000
    ANTHROPIC_LONG_CONTEXT_PRICING = {}
    # Background of the toolbar button pressed most recently (the "last
    # pressed" group — see App._track_toolbar_presses).
    TOOLBAR_ACTIVE_BG = "#add8e6"   # Tk's "light blue"
    # Fill of the SKILLS band over the Skills Manager's list (see
    # list_title_band): the blue a clicked ttk heading shows
    LIST_TITLE_BG = "#bcdcf4"

    class _NoWaitTimer:
        """Stub of helpers.input_wait_timer: nothing to accumulate without the package."""

        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    def input_wait_timer(app):
        return _NoWaitTimer()

    def rotate_log_if_needed(log_path, max_bytes):
        return False  # no myagent package -> no rotation; the log just grows

    def parse_output_cap(msg):
        """The ceiling named by an over-cap max_tokens 400 (myagent/helpers.py)."""
        mt = re.search(r"max_tokens:\s*(\d[\d,]*)\s*>\s*(\d[\d,]*)", msg or "")
        cap = int(mt.group(2).replace(",", "")) if mt else 0
        return cap if cap > 0 else None

    def strip_old_images(messages, keep=None):
        return 0   # no myagent package -> a 413 falls through to the round trim

    def connection_error_backoff(attempt):
        return min(2 ** attempt * 5, 60)   # myagent/retry_util.py's schedule

    class MCPMixin:
        pass

    class GmailMixin:
        pass

    class ProtonMailMixin:
        pass

    class OutlookMixin:
        pass

    class DocumentMixin:
        pass

    class FileMixin:
        pass

    class DesktopMixin:
        pass

    class BrowserMixin:
        pass

    class ExcelMixin:
        pass

    class PhysicalMixin:
        pass

    class VoiceMixin:
        pass

# read_document rides along unconditionally, like MyAgent's TOOLS; the schema
# is MyAgent's own (an empty list without the package).
READ_DOCUMENT_TOOLS = [t for t in _MYAGENT_TOOLS if t["name"] == "read_document"]

# Windows PowerShell writes redirected output in the OEM code page while
# Python's text mode decoded it as ANSI (cp1252): non-ASCII came back garbled,
# and a byte cp1252 leaves undefined killed communicate()'s reader thread, so
# the WHOLE stdout came back None — "[No output]". run_powershell tells
# PowerShell to write UTF-8 (no BOM; a console-less host just keeps its
# default) on its own line, so an error's char position in the command stays
# true, and decodes as UTF-8 with replacement (MyAgent's fix of 2026-09-28).
_PS_UTF8_OUTPUT = ("try { [Console]::OutputEncoding = "
                   "New-Object System.Text.UTF8Encoding $false } catch {}")

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
# The Desktop tools are DesktopMixin's since 2026-10-14 (it imports pyautogui
# and ImageGrab itself): without the myagent package there are none to offer.
_HAS_DESKTOP = _HAS_DESKTOP and _HAS_MYAGENT_TOOLS

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
            "Manage the shared skills library on disk (the skills/ SKILL.md tree, shared "
            "with MyAgent). Skills can be injected into the system prompt (enabled), "
            "retrieved on demand (on_demand), or inactive (disabled). Each skill may carry "
            "a short description (what it does + when to use it), listed in the system "
            "prompt for on_demand skills as the trigger signal. "
            "Actions: list, read, create, update, delete (the skill and its SKILL.md) — "
            "plus list_files, read_file, write_file, delete_file for the BUNDLED RESOURCE "
            "FILES a skill's folder may carry beside SKILL.md (references/, scripts/, "
            "tests/, …), addressed by skill name + a RELATIVE file_path inside the folder, "
            "so a complete Agent-Skills-style skill (SKILL.md + resources) can be authored "
            "with this one tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "read", "create", "update", "delete",
                             "list_files", "read_file", "write_file", "delete_file"],
                    "description": "The operation to perform",
                },
                "file_path": {
                    "type": "string",
                    "description": (
                        "RELATIVE path of a bundled resource file inside the skill's "
                        "folder (required for read_file / write_file / delete_file), "
                        "e.g. 'references/palette.md' or 'scripts/run.py'. Absolute "
                        "paths, '..' and SKILL.md itself are refused; subdirectories "
                        "are created on write and pruned when emptied by a delete."
                    ),
                },
                "file_content": {
                    "type": "string",
                    "description": (
                        "The full text content to write (required for write_file; "
                        "UTF-8, overwrites an existing file atomically)."
                    ),
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
                    "description": "Adaptive thinking mode for newer models (optional; create inherits current). Fable/Mythos 5 and 5.1 are always-on — 'off' is invalid there, use 'adaptive'.",
                },
                "desktop": {"type": "boolean", "description": "Bundle Desktop tools ON (default false on create)"},
                "browser": {"type": "boolean", "description": "Bundle Browser tools ON (default false on create)"},
                "meta": {"type": "boolean", "description": "Bundle Meta tools ON (default false on create)"},
                "mcp": {"type": "boolean", "description": "Bundle MCP tools ON (default false on create)"},
                "google": {"type": "boolean", "description": "Bundle Gmail tools ON (default false on create)"},
                "proton": {"type": "boolean", "description": "Bundle Proton/IMAP mail tools ON (default false on create)"},
                "outlook": {"type": "boolean", "description": "Bundle Outlook tools ON (default false on create)"},
                "excel": {"type": "boolean", "description": "Bundle the Excel live-workbook tools ON (default false on create)"},
                "physical": {"type": "boolean", "description": "Bundle the Physical tools (camera_capture, microphone_listen) ON (default false on create)"},
                "exit": {"type": "boolean", "description": "Bundle the exit_conversation (close this chatbot instance) tool ON (default false on create)"},
                "dictation_auto_send": {"type": "boolean", "description": "Bundle the Mike button's Auto-send box ON — a dictated message is sent the moment its transcript lands (default false on create)"},
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

# Exit tool — the duo's way out that IS a shutdown (2026-10-14; it replaced the
# pause / rest tool, which switched auto-chat off and left both windows open).
# Auto-chat re-injects every completed turn into the peer forever, so the only way
# a self-chat could genuinely END was killing a process — and, given run_command,
# duo chats always ended in a model-driven taskkill. This tool is that exit done
# properly: the model-callable [X] (the chat saved, the cost logged, the peer
# closed with it). Gated by the Exit checkbox (wired like MyAgent's Convo toggle:
# a per-environment behavioural affordance, persisted in app_state.json and the
# prompt bundle). The description states what the tool does, not when to use it,
# so a duo's end stays the model's own choice.
EXIT_TOOLS = [
    {
        "name": "exit_conversation",
        "description": (
            "End the conversation by closing this chatbot instance — your own "
            "process. When the current turn ends, this instance saves its chat, "
            "logs its API cost and closes its window; the process exits. A "
            "connected partner instance is closed the same way (a duo always "
            "shuts down as a pair), so the conversation is over for both. "
            "Anything you write after this call is kept in the saved chat but is "
            "NOT delivered to your partner. There is no resume: the human would "
            "have to start a new session. With no partner connected, this closes "
            "this window alone."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Optional short note on why the conversation is ending (shown to the human).",
                }
            },
        },
    },
]

# DESKTOP_TOOLS and BROWSER_TOOLS are MyAgent's (myagent.constants, try-imported
# above) since 2026-10-14: SelfBot inherits DesktopMixin / BrowserMixin, so the
# schemas and the implementations stay one set — see CLAUDE_SELFBOT.md's
# "Tool parity with MyAgent".

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
        r"\breg(?:\.exe)?\s+delete\b",
        # Registry deletes under HKLM / HKCU, in PowerShell's own path forms
        # (HKLM:\…, Registry::HKEY_LOCAL_MACHINE\…) — MyAgent's patterns. The
        # four these replaced wanted a literal "\\HKLM" that no real path has,
        # so they never matched.
        r"\bRemove-Item(?:Property)?\b.*\b(?:HKLM|HKCU|HKEY_LOCAL_MACHINE|HKEY_CURRENT_USER)\b",
        # shutdown.exe at command position with any switch but /a (abort).
        r"(?:^|[\n;&|({])\s*shutdown(?:\.exe)?\s+[/-](?!a\b)",
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
        # Built-in aliases of the cmdlets above, matched only at command
        # position — line start or after ; & | ( { — so the word in a string
        # or a path doesn't trip them (MyAgent's list). APPENDED, never
        # renamed: a saved bypass list names patterns by their text.
        # (`start`, Start-Process's everyday alias, is deliberately not here.)
        r"(?:^|[\n;&|({])\s*(?:ri|erase)(?:\s|$)",      # Remove-Item
        r"(?:^|[\n;&|({])\s*(?:mi|move|mv)(?:\s|$)",    # Move-Item
        r"(?:^|[\n;&|({])\s*(?:rni|ren)(?:\s|$)",       # Rename-Item
        r"(?:^|[\n;&|({])\s*(?:sc|clc)(?:\s|$)",        # Set-Content / Clear-Content
        r"(?:^|[\n;&|({])\s*spps(?:\s|$)",              # Stop-Process
        r"(?:^|[\n;&|({])\s*saps(?:\s|$)",              # Start-Process
        r"\b(?:iwr|curl|wget)\b.*-OutFile",         # Invoke-WebRequest to a file
        r"\biex\s*\(",                              # Invoke-Expression with (…)
        r"\bRemove-ItemProperty\b",                 # registry values (HKLM/HKCU: blocked)
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
    "claude-opus-5-5",
    "claude-opus-4-8",
    "claude-fable-5-1",
    "claude-fable-5",
    "claude-sonnet-5-5",    # the current Sonnet (live 2026-09-28): $2/$10, cache reads 0.05x, Off = between_tools — see _is_sonnet_55
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-5-5",
    "claude-haiku-4-5-20251001",
]
DEFAULT_MODEL = FALLBACK_MODELS[0]
# Every call's max_tokens is the model's OWN output ceiling — the startup
# listing's figure for the id, else ANTHROPIC_MAX_OUTPUT_TOKENS' family row,
# else ANTHROPIC_MAX_OUTPUT_DEFAULT (_anthropic_output_cap; MyAgent's rule of
# 2026-10-11, here since 2026-10-14). Adaptive thinking has no budget of its
# own, so the 32,768 every thinking call used to send (8,192 with thinking
# off) was the only bound on reasoning, and a long thinking phase could be
# cut off at it — billed and lost.
# Betas every call carries (server-side web search + code execution, and the
# Files API for the code-execution file outputs). The Fable/Mythos class
# appends its own two — see _fable_features.
ANTHROPIC_BETAS = ["web-search-2025-03-05", "code-execution-2025-08-25", "files-api-2025-04-14"]
# Prompt-cache breakpoint marker. 5-minute TTL (the default) is deliberate:
# writes cost 1.25x and match ANTHROPIC_PRICING's cache-write column exactly,
# so _get_pricing's four-bucket cost stays accurate. A "ttl": "1h" variant
# would need the extended-cache-ttl-2025-04-11 beta and 2x writes.
CACHE_CONTROL = {"type": "ephemeral"}
# Deprecated / soon-to-be-retired model id prefixes hidden from the picker.
# _fetch_available_models filters the live models.list() against these, so newer
# models appear automatically and only the retiring ones drop out. Opus 4.5 /
# Haiku 4.5 stay — still active. The dated 4.0 ids are claude-opus-4-20250514 /
# claude-sonnet-4-20250514, matched by the "-4-20" prefix (a real "-4-20" minor
# is implausible — minors run 5, 6, 7, 8…). The policy (2026-07-27): a model
# leaves the picker the day its retirement is ANNOUNCED, not when it arrives —
# the Models API's `lifecycle` field still said "active" for Sonnet 4.5 two
# weeks after the deprecations page listed it.
DEPRECATED_MODEL_PREFIXES = (
    "claude-sonnet-4-5",     # Sonnet 4.5 — deprecated 2026-09-30, retires 2026-11-30 (→ claude-sonnet-5-5); the 2026-10-14 audit
    "claude-opus-4-1",       # Opus 4.1 — retired 2026-08-05
    "claude-opus-4-0",       # Opus 4.0 alias
    "claude-opus-4-20",      # Opus 4.0 dated id (claude-opus-4-20250514)
    "claude-sonnet-4-0",     # Sonnet 4.0 alias
    "claude-sonnet-4-20",    # Sonnet 4.0 dated id (claude-sonnet-4-20250514)
    "claude-3",              # every Claude 3.x — retired/deprecated
    "claude-2",              # Claude 2.x — retired
)
# Exact-match aliases for adaptive-thinking models; the version-parsed
# _is_adaptive_model backstops dated snapshots and future Opus/Sonnet 4.6+ minors.
ADAPTIVE_THINKING_MODELS = {"claude-fable-5-1", "claude-mythos-5-1",
                            "claude-fable-5", "claude-mythos-5",
                            "claude-opus-5-5", "claude-opus-5",
                            "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                            "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6",
                            "claude-haiku-5-5"}
# Claude 5 Mythos-class (Fable 5 / 5.1, Mythos 5 / 5.1): thinking is ALWAYS ON —
# the API rejects thinking={"type": "disabled"} and budget_tokens with HTTP 400,
# and sampling params (temperature/top_p/top_k) are rejected unconditionally.
# Prefix-matched, so claude-fable-5-1 (2026-08-28) needed no new entry.
ALWAYS_ON_THINKING_PREFIXES = ("claude-fable-", "claude-mythos-")
# Claude Opus 5.5 and every later Opus join the always-on class BY VERSION
# (MyAgent's ANTHROPIC_ALWAYS_ON_OPUS_MIN): an explicit disable is HTTP 400
# there too, and they take the preserved-thinking binding and the refusal
# fallbacks (_fable_features).
ALWAYS_ON_OPUS_MIN = (5, 5)
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
        store_unreadable as _store_unreadable,
        absorb_conflict_forks as _absorb_conflict_forks,
        resolve_skills_dir as _resolve_skills_dir,
        load_skills_tree as _load_skills_tree,
        save_skills_tree as _save_skills_tree,
        delete_skill_tree_entry as _delete_skill_tree_entry,
        copy_skill_resources as _copy_skill_resources,
        list_skill_resources as _list_skill_resources,
        skill_resource_path as _skill_resource_path,
        skill_dir_for as _skill_dir_for,
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

    def _store_unreadable(path):
        # datapaths.store_unreadable: the file EXISTS but is not a JSON object
        if not os.path.exists(path):
            return False
        try:
            with open(path, encoding="utf-8") as f:
                return not isinstance(json.load(f), dict)
        except (OSError, ValueError):
            return True

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

    # Bundled-resource-file stubs (mirror myagent.datapaths, 2026-09-25): the
    # folder is the skill — SKILL.md may sit beside references/, scripts/, …
    # which sync, copy and delete with it. Folders resolve by FRONTMATTER
    # name, as the loader reads them.
    def _skill_dir_for(dirpath, name):
        if not os.path.isdir(dirpath):
            return None
        for sub in sorted(os.listdir(dirpath)):
            md = os.path.join(dirpath, sub, "SKILL.md")
            if not os.path.isfile(md):
                continue
            try:
                with open(md, encoding="utf-8-sig") as f:
                    meta, _ = _sb_skill_parse(f.read())
            except OSError:
                continue
            if (" ".join((meta.get("name") or "").split()) or sub) == name:
                return os.path.join(dirpath, sub)
        return None

    def _sb_is_skill_md_family(fname):
        # Case-insensitive, like datapaths': on Windows / macOS "skill.md" IS SKILL.md.
        low = fname.lower()
        return (low == "skill.md" or low.startswith("skill.md.")
                or (low.startswith("skill-") and low.endswith(".md")))

    def _list_skill_resources(dirpath, name):
        folder = _skill_dir_for(dirpath, name)
        if folder is None:
            return None
        rels = []
        for root, dirs, files in os.walk(folder):
            dirs.sort()
            rel_root = os.path.relpath(root, folder)
            for fname in sorted(files):
                if rel_root == os.curdir and _sb_is_skill_md_family(fname):
                    continue
                rels.append(fname if rel_root == os.curdir
                            else os.path.join(rel_root, fname))
        return rels

    def _skill_resource_path(dirpath, name, rel_path):
        folder = _skill_dir_for(dirpath, name)
        if folder is None:
            return None, (f"skill '{name}' has no folder on disk yet — save the "
                          "skill first")
        rel = (rel_path or "").replace("\\", "/").strip()
        if not rel or rel.endswith("/"):
            return None, "file_path must name a file, not a directory"
        if rel.startswith("/") or os.path.isabs(rel) or re.match(r"[A-Za-z]:", rel):
            return None, "file_path must be RELATIVE to the skill's folder"
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts or ".." in parts:
            return None, "file_path may not leave the skill's folder ('..')"
        if any(":" in p for p in parts):
            return None, "file_path must be RELATIVE to the skill's folder (no ':')"
        if len(parts) == 1 and _sb_is_skill_md_family(parts[0]):
            return None, ("SKILL.md (and its conflict forks) are managed by the "
                          "create/update actions, not as resource files")
        return os.path.join(folder, *parts), None

    def _copy_skill_resources(dirpath, src_name, dst_name):
        # The Skills Manager's save-as resource copy: everything but the root
        # SKILL.md family, never overwriting a destination file.
        import shutil
        src, dst = _skill_dir_for(dirpath, src_name), _skill_dir_for(dirpath, dst_name)
        if src is None or dst is None or os.path.realpath(src) == os.path.realpath(dst):
            return []
        copied = []
        for rel in _list_skill_resources(dirpath, src_name) or []:
            target = os.path.join(dst, rel)
            if os.path.exists(target):
                continue
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(os.path.join(src, rel), target)
            except OSError:
                continue
            copied.append(rel)
        return copied

PROMPTS_FILE = _resolve_store("system_prompts.json")
# Same cost log MyAgent writes: APICostLog_<machine>.txt in the OneDrive share
# (per-machine files — appends never conflict-fork, every machine's spend syncs
# everywhere for the viewers to aggregate), repo-root APICostLog.txt fallback.
APICOST_LOG_FILE = _resolve_costlog()
CHATS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved_chats")
APP_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_state.json")
APP_STATE_FILE_2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_state_2.json")
# The in-progress cost record (MyAgent's agent_run_<N>.json, here since
# 2026-10-14): selfbot_run_<1|2>.json beside the state file, per machine,
# gitignored — rewritten after every call and removed by the close, so one
# found at launch is a session killed from outside (_run_progress_fold_in).
SELFBOT_RUN_PREFIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot_run_")
# The three persisted dialogs: kind → the App attribute holding its open Toplevel (or
# None). Each kind also has a `_last_<kind>_geometry` attribute, its last-known
# geometry on the CURRENT monitor layout, and a legacy flat state key from before
# the per-layout `dialog_geometries` dict (2026-09-16), adopted once on load.
DIALOG_WINDOWS = {
    "prompt_editor": "prompt_editor_window",
    "skills_dialog": "skills_editor_window",
    "ps_safety": "_ps_safety_dialog",
}
DIALOG_LEGACY_KEYS = {
    "prompt_editor": "prompt_editor_geometry",
    "skills_dialog": "skills_dialog_geometry",
    "ps_safety": "ps_safety_dialog_geometry",
}
GEOMETRY_RE = r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)"   # Tk's "WxH+X+Y" (X / Y may be negative)

# Windows only (Tk 8.6's Windows port): once a window is on screen its SIZE is
# compared with the one asked for and corrected by the residual, at most
# GEOMETRY_SETTLE_PASSES times — see _set_geometry. A residual beyond
# GEOMETRY_SETTLE_MAX_DELTA px on either axis is no frame difference (a
# minsize, a monitor edge) and is left alone rather than chased.
GEOMETRY_SETTLE_PASSES = 3
GEOMETRY_SETTLE_MAX_DELTA = 100
MAIN_MIN_W, MAIN_MIN_H = 400, 300                # below this a saved main geometry is junk (an unmapped root reports 1x1)
SKILLS_DIR = _resolve_skills_dir()  # per-skill SKILL.md tree; a legacy skills.json migrates in on first load
STORES_SYNCED = os.path.dirname(PROMPTS_FILE) != os.path.dirname(os.path.abspath(__file__))


class _UnreadableStore(dict):
    """The session-only stand-in _load_saved_prompts serves while the prompt
    store file exists but cannot be read or parsed. It reads like the store,
    but _save_prompts_to_disk refuses to write it (MyAgent's twin guards
    agent_instructions.json)."""


LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot.lock")
INJECT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot_inject.txt")
AUTO_MSG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot_auto_msg.json")
# A duo window's busy mark while it answers a message its partner waits for
# (_set_busy): selfbot_busy_1.flag for instance 1, selfbot_busy_2.flag for 2.
DUO_BUSY_PREFIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selfbot_busy_")



# ── List title band (2026-09-12) — an in-file copy of ui_mixin's ──────────
# The SKILLS band over the Skills Manager's list is the band MyAgent's two
# lists wear (the why — a native ttk heading lights up on hover and click and
# cannot hold one look — is at ui_mixin.list_title_band). Byte-identical to
# the original by tests/test_selfbot_list_title_band.py (SelfBot's convention:
# the myagent import is the optional kind); LIST_TITLE_BG is try-imported from
# myagent.constants with a literal fallback in the stub block above.
def list_title_band(parent, text):
    """The uppercase title band over a list — INSTRUCTIONS (Instruction
    Editor), SKILLS (Skills Manager). Returned ungridded: the caller places
    it in the list's column."""
    return tk.Label(parent, text=text.upper(), anchor="w", font=("Arial", 10),
                    background=LIST_TITLE_BG, padx=5, pady=4, borderwidth=0)


# ── Button focus ring on macOS (2026-09-15) — an in-file copy of keyboard.py's ──
# Tk 9.0.3 on macOS 26 draws a focused tk.Button's own 1-px focus ring shifted
# by the parent frame's offset: an L-shaped black line struck through a button
# whose frame sits near the window's top-left (the Skills Manager's SAVE /
# DELETE / NEW, at (10, 10)), no ring at all on the main window's seven. From
# 3 px Aqua draws the ring itself, in the right place. Byte-identical to
# myagent/keyboard.py's by tests/test_selfbot_focus_ring.py (SelfBot's
# convention: the myagent import is the optional kind); called first in
# setup_ui, before any button exists, because the option database only fills
# options at creation. The Auto toggle's explicit highlightthickness=0 wins.
def install_focus_ring_defaults(root):
    """Once per Tk instance, BEFORE any button exists: a focus ring Tk can draw.

    A focused tk.Button wears a ring `highlightthickness` wide. Under 3 Tk
    draws that ring itself, and Tk 9.0.3 on macOS 26 (2026-09-15) draws it
    shifted up-left by the parent frame's offset within the window: on a
    button whose frame sits within a button's size of the window's top-left
    corner only the ring's bottom and right edges land inside the widget — a
    stray black line struck through the label with a tick at one corner (the
    artefact over MyAgent's Instruction at startup, which holds the initial
    focus, and over SelfBot's Skills Manager SAVE / DELETE / NEW) — and on
    any button further down the ring misses the widget entirely, so there is
    no focus cue at all (SelfBot's main-window buttons). From 3 up Tk leaves
    the ring to Aqua, which draws its own rounded blue one in the right place
    (probed live: 1 and 2 garbage or nothing; 3, 4 and 6 the system ring).
    So on Aqua the option database gives every LATER tk.Button a thickness
    of 3. An explicit `highlightthickness=` on a widget still wins (SelfBot's
    Auto toggle keeps its 0), a widget that already exists is not touched,
    and no other class is (ttk buttons have no such option; checkbuttons
    draw nothing wrong at their default). Windows and X11 draw their default
    ring correctly (the dotted rectangle) and are left alone. One deliberate
    side effect on the Mac: the ring area is also the frame the toolbar
    paints light blue for its "last pressed" button, so that frame is 3 px
    there, and each button grows 2 px per side.
    """
    if root.tk.call("tk", "windowingsystem") == "aqua":
        root.option_add("*Button.highlightThickness", 3)


# ── Scrollbars out of the Tab order (2026-09-26) — an in-file copy of keyboard.py's ──
# Tk's fallback focus rule counts a viewable, enabled widget whose class has a
# Key binding, and Tk 9.0.3's Scrollbar class binds Page Up / Page Down, so on
# the Mac each of SelfBot's six tk.Scrollbars (chat display, System Prompt
# editor, Skills Manager list + text, Safety dialog, confirm-command dialog) was
# a Tab stop. Byte-identical to myagent/keyboard.py's by
# tests/test_selfbot_focus_ring.py; called right after install_focus_ring_defaults
# in setup_ui, before any scrollbar exists.
def install_scrollbar_defaults(root):
    """Once per Tk instance, BEFORE any scrollbar exists: none is a Tab stop.

    A widget whose `takefocus` is unset is a Tab stop by Tk's fallback rule
    (`tk::FocusOK`): viewable, not disabled, and its class carries some Key
    binding. Tk 9.0.3's Scrollbar class binds Page Up / Page Down, so on the
    Mac (2026-09-26) Tab from the read-only output pane landed on its
    scrollbar instead of Voice Setup, and the same rule put every scrollbar
    created right after a text box — the editor's, the Skills Manager's, the
    dialogs' — in the path of Escape's `focus_next`. A scrollbar has nothing
    to offer the keyboard (the widget it scrolls takes the same keys), so the
    option database gives every LATER tk.Scrollbar a `takefocus` of 0. On
    every platform: the intended Tab order is the same everywhere, and where
    a scrollbar was never a stop this changes nothing. An explicit
    `takefocus=` on a widget still wins, a scrollbar that already exists is
    not touched, and ttk scrollbars (class TScrollbar) are not covered —
    MyAgent has none.
    """
    root.option_add("*Scrollbar.takeFocus", "0")


def _get_window_pid(hwnd):
    """Get the process ID that owns a given window handle."""
    if IS_WINDOWS:
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    return 0


class App(MCPMixin, GmailMixin, ProtonMailMixin, OutlookMixin, DocumentMixin, FileMixin,
          DesktopMixin, BrowserMixin, ExcelMixin, PhysicalMixin, VoiceMixin):
    def __init__(self, root):
        self.root = root
        self.root.title("Claude SelfBot")
        self._set_geometry(self.root, DEFAULT_GEOMETRY)

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
        self._session_calls = 0   # API calls made by this process (the "Call #N" counter, summed across every message) — logged beside the cost
        # Cumulative token buckets for this process, logged as the cost line's
        # 9th–12th fields (2026-09-14) in the same positions MyAgent writes
        # them. input is the NON-cached count and the two cache buckets are
        # disjoint, so in+write+read is the true billed input volume.
        self._session_tokens_in = 0
        self._session_tokens_out = 0
        self._session_tokens_cache_write = 0
        self._session_tokens_cache_read = 0
        # The rest of the cost line's arguments, kept like MyAgent's run
        # accumulators (cost collection analogous to MyAgent's, 2026-10-14 —
        # CLAUDE_SELFBOT.md's API cost tracking paragraph):
        self.provider = "Anthropic"       # the line's 2nd field; SelfBot is Anthropic-only
        self._session_had_usage = False   # a call answered with usage, priced or not (the zero-cost gate)
        self._session_per_model = {}      # served model -> [cost, in, out, cache_write, cache_read] (the 13th field)
        self._session_work_secs = 0.0     # the model's working seconds: every finished reply, minus dialog waits
        self._reply_started = None        # time.monotonic() of the reply in flight, else None
        self._input_wait_secs = 0.0       # helpers.input_wait_timer's accumulator, zeroed per reply
        self._unpriced_noted = set()      # models already announced as having no pricing row
        self.pending_images = []  # list of (base64_data, media_type, filename)
        self._screenshot_scale = 1.0  # ratio to convert image coords → screen coords
        self._screenshot_offset = (0, 0)  # display origin offset for per-display screenshots
        self._screenshot_dims = (0, 0)    # (width, height) of last screenshot sent to model
        # The per-display coordinate state DesktopMixin keeps (MyAgent's, here
        # since 2026-10-14): the most recent capture per display, the most
        # recent FULL capture per display (a region screenshot is read in that
        # space) and the last resized screenshot's bytes.
        self._display_states = {}
        self._display_images = {}
        self._display_full_states = {}
        self._display_full_images = {}
        self._last_screenshot_bytes = None
        # The native file tools' read-before-edit memory (FileMixin): the paths
        # read_file has shown this conversation; a new chat starts over.
        self._file_read_paths = set()
        self.debug_enabled = tk.BooleanVar(value=False)
        self.tool_calls_enabled = tk.BooleanVar(value=False)
        self.show_activity = tk.BooleanVar(value=False)
        self.show_thinking = tk.BooleanVar(value=False)
        self.save_thinking = tk.BooleanVar(value=False)
        self.desktop_enabled = tk.BooleanVar(value=False)
        self.browser_enabled = tk.BooleanVar(value=False)
        # MyAgent's Excel (xlwings) and Physical (camera / microphone) sets, and
        # its Diag toggle for the desktop tools' coordinate-trail lines (2026-10-14)
        self.excel_enabled = tk.BooleanVar(value=False)
        self.physical_enabled = tk.BooleanVar(value=False)
        self.diag_enabled = tk.BooleanVar(value=False)
        # Voice input (MyAgent's VoiceMixin, 2026-10-14): the Auto-send box
        # beside the Mike button under the message box — a dictated message
        # is sent the moment its transcript lands. Kept like the other
        # toggles: in app_state.json and in a saved prompt's bundle.
        self.dictation_auto_send = tk.BooleanVar(value=False)
        self._dictation = None            # the row's _VoiceDictation, set by setup_ui
        # MyAgent-style tool subsystems (Meta / MCP / Google / IMAP / Outlook).
        self.meta_enabled = tk.BooleanVar(value=False)
        self.mcp_enabled = tk.BooleanVar(value=False)
        self.google_enabled = tk.BooleanVar(value=False)
        self.proton_enabled = tk.BooleanVar(value=False)
        self.outlook_enabled = tk.BooleanVar(value=False)
        # SelfBot-native Exit tool (see EXIT_TOOLS) — lets the model end a
        # self-chat by closing its own instance, the way [X] does.
        self.exit_enabled = tk.BooleanVar(value=False)
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
        # Speech-to-text clients for the Mike button, Voice Setup's Test and
        # the Physical set's microphone_listen (sounddevice itself is imported
        # at the first press, never here): VoiceMixin transcribes with the
        # Voice Setup provider (OpenAI by
        # default; xAI; Google) through self.openai_client / xai_client /
        # gemini_client, built here from the keys in the environment when the
        # SDK is installed. The CHAT stays Anthropic-only — these transcribe
        # audio and nothing else (2026-10-14).
        self.openai_client = self.xai_client = self.gemini_client = None
        if _HAS_MYAGENT_TOOLS:
            try:
                import openai
                if os.environ.get("OPENAI_API_KEY"):
                    self.openai_client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
                if os.environ.get("XAI_API_KEY"):
                    self.xai_client = openai.OpenAI(api_key=os.environ["XAI_API_KEY"],
                                                    base_url="https://api.x.ai/v1")
            except Exception:
                pass
            try:
                from google import genai
                gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
                if gemini_key:
                    self.gemini_client = genai.Client(api_key=gemini_key)
            except Exception:
                pass
        self._playwright = None
        self._browser = None
        self._page = None
        self._edge_process = None
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        self.system_prompt_name = ""
        # The system prompt a reply's calls send, frozen when stream_worker
        # starts and let go when it ends (_freeze_system_prompt); None between.
        self._run_system_prompt = None
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
        # Served id -> output ceiling, from the startup listing or a 400 that
        # named a lower one (see _anthropic_output_cap).
        self._anthropic_output_caps = {}
        # The last dropped-thinking notice's shape (count, reasons) — said once
        # per conversation; a new chat resets it. And the second-rate-card ⚠,
        # said once per process.
        self._thinking_drops_noted = None
        self._long_context_noted = False
        # Fable/Mythos beta surfaces ("block_binding", "fallbacks") this API key
        # turned out not to be enrolled in (a 400 naming them). Learned once per
        # process by the stream_worker 400 rungs; _fable_features stops sending.
        self._anthropic_unsupported = set()
        self.prompt_editor_window = None
        self._last_prompt_editor_geometry = None    # persisted System Prompt Editor geometry
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
        self._held_for_partner = False  # a typed message waits in the box for the partner's answer
        self._stashed_draft = ""        # a draft set aside while an arriving message is sent
        self._pass_pending = False      # this window's reply is on its way to the partner
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
        # A record the slot's previous owner left behind means that SelfBot
        # died with calls unlogged (killed from outside — the duo's taskkill, a
        # crash, a reboot): its cost line is written now, from the record,
        # before anything else can be logged (MyAgent's fold-in, 2026-10-08).
        self._run_progress_fold_in()
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
        # A busy mark a killed predecessor left in this slot would keep the
        # partner holding every message typed there (_awaiting_partner).
        self._set_busy(False)
        # Either instance: poll for the opener the partner window's human typed
        # (_write_inject_file) — the conversation may be opened in either window
        self.root.after(500, self._poll_inject_file)
        if self._is_second_instance:
            # Retry loading names if they came up empty (race with instance 1)
            self.root.after(2000, self._retry_load_names)
        # Poll for peer instance to enable/disable auto-chat and send delay
        self.root.after(2000, self._poll_for_peer)
        # Poll for auto-injected messages from the other instance
        self.root.after(500, self._poll_auto_msg)

    def setup_ui(self):
        # Before the first button or scrollbar exists (the in-file copies
        # above): on macOS the option database gives every tk.Button a focus
        # ring Aqua draws, and every tk.Scrollbar stays out of the Tab order.
        install_focus_ring_defaults(self.root)
        install_scrollbar_defaults(self.root)

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

        # The main window's seven plain buttons (DELETE / NEW CHAT here, SAVE on
        # row 2, the four on the button bar) are a "last pressed" group with one
        # shared font — their commands are wired by _track_toolbar_presses at
        # the end of the button bar, through the highlighter. The red/green
        # Auto toggle stays out of it: its colour IS its state.
        toolbar_font = ("Arial", 10)
        self.new_chat_button = tk.Button(model_toolbar, text="NEW CHAT", width=10, font=toolbar_font)
        self.new_chat_button.pack(side=tk.RIGHT, padx=(5, 0))
        self.delete_chat_button = tk.Button(model_toolbar, text="DELETE", width=8, font=toolbar_font)
        self.delete_chat_button.pack(side=tk.RIGHT, padx=(10, 5))

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

        self.save_chat_button = tk.Button(chat_toolbar, text="SAVE", width=6, font=toolbar_font)
        self.save_chat_button.pack(side=tk.LEFT, padx=(0, 15))

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

        # Scrollbar (not a Tab stop: install_scrollbar_defaults)
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

        # Voice input (MyAgent's, 2026-10-14): Mike, the Auto-send box and the
        # status line under the message box — the row MyAgent's Agent Request
        # dialog carries under its reply box, built by the inherited
        # _voice_build_row over SelfBot's own send path (_send_as_enter) and
        # Auto-send setting. The root is the row's window, so its <Destroy>
        # releases the microphone when the app closes. Without the myagent
        # package there is no VoiceMixin, hence no row. See CLAUDE_SELFBOT.md's
        # Voice input paragraph.
        if _HAS_MYAGENT_TOOLS:
            self.voice_row, self.mike_button, self.auto_send_toggle, self._dictation = (
                self._voice_build_row(self.root, self.input_field,
                                      self.dictation_auto_send, self._send_as_enter))
            self.voice_row.grid(row=5, column=0, columnspan=2, sticky="ew",
                                padx=10, pady=(0, 5))

        # Button bar
        button_frame = tk.Frame(self.root)
        button_frame.grid(row=6, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self.attach_button = tk.Button(
            button_frame, text="Attach Images", width=14, font=toolbar_font,
        )
        self.attach_button.pack(side=tk.LEFT, padx=(5, 5))

        self.prompt_button = tk.Button(
            button_frame, text="System Prompt", width=14, font=toolbar_font,
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
            button_frame, text=skills_label, padx=10, font=toolbar_font,
        )
        self.skills_button.pack(side=tk.LEFT, padx=(5, 5))

        self.ps_safety_button = tk.Button(
            button_frame, text="Safety", padx=10, font=toolbar_font,
        )
        self.ps_safety_button.pack(side=tk.LEFT, padx=(0, 5))
        self._update_ps_safety_button()

        self._track_toolbar_presses(
            (self.delete_chat_button, self._delete_chat),
            (self.new_chat_button, self._new_chat),
            (self.save_chat_button, self._save_chat),
            (self.attach_button, self.attach_image),
            (self.prompt_button, self.open_prompt_editor),
            (self.skills_button, self.open_skills_editor),
            (self.ps_safety_button, self._open_ps_safety_dialog),
        )

        # Checkbox row (below buttons)
        checkbox_frame = tk.Frame(self.root)
        checkbox_frame.grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 5))

        # Voice Setup at the row's left, where MyAgent's main window has it
        # (left of Debug, created first so Tab reaches it first): the
        # inherited dialog — provider / model / language / vocabulary hint /
        # microphone + Test — over the per-user settings file MyAgent's uses.
        # Disabled without the myagent package, whose VoiceMixin opens it.
        self.voice_setup_button = tk.Button(
            checkbox_frame, text="Voice Setup", font=("Arial", 9),
            command=lambda: self._voice_setup_from_main(),
        )
        self.voice_setup_button.pack(side=tk.LEFT, padx=(5, 5))
        if not _HAS_MYAGENT_TOOLS:
            self.voice_setup_button.config(state=tk.DISABLED)

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

        # MyAgent's Diag toggle: the desktop tools' [DIAG capture] / [DIAG click]
        # coordinate-trail lines (DesktopMixin reads diag_enabled), off by default
        self.diag_toggle = tk.Checkbutton(
            checkbox_frame, text="Diag", variable=self.diag_enabled,
            font=("Arial", 9),
        )
        self.diag_toggle.pack(side=tk.LEFT, padx=(5, 0))

        # Tool row (below the Debug/display-toggle row) — Desktop / Browser plus the
        # MyAgent-style subsystems Meta / MCP / Google / IMAP / Outlook and the
        # SelfBot-native Exit (close-this-instance) tool. Anthropic-only; each is
        # disabled when its optional libraries are absent (Meta and Exit need none).
        # Proton is labelled IMAP. Left-aligned (sticky="w") to match the Debug row above.
        tools_frame = tk.Frame(self.root)
        tools_frame.grid(row=8, column=0, columnspan=2, sticky="w", pady=(0, 5))

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
        if not _HAS_MYAGENT_TOOLS:
            # BrowserMixin's tools (Playwright is imported at the first call,
            # so only the package's absence disables the box, like Desktop's)
            self.browser_enabled.set(False)
            self.browser_toggle.config(state=tk.DISABLED)

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

        # MyAgent's Excel (xlwings) and Physical (camera via OpenCV, microphone
        # via sounddevice) sets — disabled when the package behind each is absent
        self.excel_toggle = tk.Checkbutton(
            tools_frame, text="Excel", variable=self.excel_enabled, font=("Arial", 9),
        )
        self.excel_toggle.pack(side=tk.LEFT, padx=(5, 0))
        if not (_HAS_MYAGENT_TOOLS and _HAS_EXCEL):
            self.excel_enabled.set(False)
            self.excel_toggle.config(state=tk.DISABLED)

        self.physical_toggle = tk.Checkbutton(
            tools_frame, text="Physical", variable=self.physical_enabled, font=("Arial", 9),
        )
        self.physical_toggle.pack(side=tk.LEFT, padx=(5, 0))
        if not (_HAS_MYAGENT_TOOLS and _HAS_PHYSICAL):
            self.physical_enabled.set(False)
            self.physical_toggle.config(state=tk.DISABLED)

        self.exit_toggle = tk.Checkbutton(
            tools_frame, text="Exit", variable=self.exit_enabled, font=("Arial", 9),
        )
        self.exit_toggle.pack(side=tk.LEFT, padx=(5, 0))

        # Attachment indicator (hidden until an image is attached)
        self.attach_label = tk.Label(
            self.root, text="", foreground="#6a1b9a", font=("Arial", 9)
        )
        self.attach_label.grid(row=9, column=0, columnspan=2)

    # --- App State Persistence ---

    def _fetch_available_models(self):
        """Fetch available models from the Anthropic API — hiding deprecated /
        soon-to-be-retired ids — and fall back to the hardcoded list."""
        try:
            response = self.client.models.list(limit=100)
            # Each served id's output ceiling — the listing's `max_tokens`,
            # the Models API's field since 2026-03 — is what every call sends
            # (_anthropic_output_cap); kept for the hidden ids too, and a
            # failed fetch leaves the dict as it was, so a ceiling a 400
            # taught survives a listing outage (MyAgent's rule of 2026-10-11).
            caps = {}
            for m in response.data:
                cap = getattr(m, "max_tokens", None)
                if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
                    caps[m.id] = cap
            self._anthropic_output_caps = caps
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
        """Fable 5 / 5.1 and Mythos 5 / 5.1 (by prefix, so new minors need no
        entry), and Claude Opus 5.5 or any later Opus (by version — an Opus
        minor of 5 or more, or a later major; a dated snapshot's date in the
        minor slot does not count): thinking is always on (disable /
        budget_tokens are HTTP 400) and sampling params are rejected
        unconditionally. MyAgent's _is_anthropic_always_on_thinking twin."""
        mid = model_id or self.model or ""
        if mid.startswith(ALWAYS_ON_THINKING_PREFIXES):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-opus-",))
        if version is None:
            return False
        major, minor = version
        if minor >= 100:
            minor = 0   # claude-opus-5-20260724: a date, not a minor
        return (major, minor) >= ALWAYS_ON_OPUS_MIN

    def _is_haiku_5(self, model_id=None):
        """True for the Haiku 5 generation — claude-haiku-5-5 (live 2026-10-07)
        and any later Haiku, version-parsed as Haiku major >= 5 so a dated
        snapshot rides along and Haiku 4.5 keeps its manual budget. The first
        Haiku with the Claude 5 request contract, every fact probed live for
        MyAgent on 2026-10-08 (_is_anthropic_haiku_5; ported 2026-10-14):
        ADAPTIVE thinking only (the budget form is HTTP 400), on by default
        when the param is omitted, an explicit disable accepted at effort
        high or below ("Off" sends no effort, so the Off rung stays), effort
        low..max, and no sampling params ('`temperature` is deprecated for
        this model', any value but 1). Read by the adaptive / on-by-default /
        temperature / max / xhigh helpers below, by _fable_features (the
        thinking binding, no fallbacks) and by _anthropic_server_tools (the
        current triple)."""
        mid = model_id or self.model or ""
        version = self._parse_claude_major_minor(mid, ("claude-haiku-",))
        return version is not None and version >= (5, 0)

    def _is_sonnet_55(self, model_id=None):
        """True for Claude Sonnet 5.5 (claude-sonnet-5-5, live 2026-09-28) and
        any later Sonnet — version-parsed as Sonnet (5, 5) or above, a dated
        snapshot's date in the minor slot not counting — the Sonnet that
        changed the off switch. Probed live for SelfBot on 2026-10-14 (the
        Models API tree says thinking.types.disabled is unsupported, then a
        15-request matrix): an explicit disable is HTTP 400 ('To turn thinking
        off on this model, send "thinking": {"type": "between_tools"}'), and
        `between_tools` — no extended thinking, the short notes between tool
        calls back as thinking blocks — is accepted at effort high or below
        only (400 at xhigh / max) and with NO other field inside `thinking`
        (display, budget_tokens, block_binding: each a 400, the binding's
        beta HEADER alone tolerated); adaptive thinking takes the
        preserved-thinking binding and the summarized display; `fallbacks:
        "default"` is accepted beside either shape while a model LIST is
        refused; the budget form and any temperature but 1 are 400; the
        20260209 server-tool triple is accepted. Read by
        _apply_thinking_params (the Off shape) and _fable_features (the
        binding and the fallbacks); the Off rung stays in the picker."""
        mid = model_id or self.model or ""
        version = self._parse_claude_major_minor(mid, ("claude-sonnet-",))
        if version is None:
            return False
        major, minor = version
        if minor >= 100:
            minor = 0   # claude-sonnet-5-20260629: a date, not a minor
        return (major, minor) >= (5, 5)

    def _is_adaptive_model(self, model_id=None):
        """Adaptive-thinking models — Opus/Sonnet 4.6+, Haiku 5+ and the
        always-on Mythos class. Version-parsed so dated snapshots and future
        minors are caught without editing ADAPTIVE_THINKING_MODELS."""
        mid = model_id or self.model or ""
        if self._is_always_on_thinking(mid) or self._is_haiku_5(mid):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-opus-", "claude-sonnet-"))
        return version is not None and version >= (4, 6)

    def _thinking_on_by_default(self, model_id=None):
        """Models that run ADAPTIVE thinking when `thinking` is omitted — Opus
        5+, Sonnet 5+, Haiku 5+ and the always-on class. For these, "Off" must
        be sent as an explicit thinking={"type": "disabled"} or the model
        silently thinks, billed, with the Show Thinking pane dark. (Opus 5 and
        Haiku 5.5 accept the explicit disable only at effort high or below —
        satisfied, "Off" sends no effort. Sonnet 5.5+ refuses the disable
        outright: its Off is `between_tools` — _is_sonnet_55.)"""
        mid = model_id or self.model or ""
        if self._is_always_on_thinking(mid) or self._is_haiku_5(mid):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-opus-", "claude-sonnet-"))
        return version is not None and version >= (5, 0)

    def _rejects_temperature(self, model_id=None):
        """Models that removed sampling params — Opus 4.7+, Sonnet 5+, Haiku 5+
        and the always-on Mythos class (a non-default temperature returns
        HTTP 400)."""
        mid = model_id or self.model or ""
        if self._is_always_on_thinking(mid) or self._is_haiku_5(mid):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-opus-",))
        if version is not None and version >= (4, 7):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-sonnet-",))
        return version is not None and version >= (5, 0)

    def _supports_max_effort(self, model_id=None):
        """'max' thinking effort — Opus 4.6+, Sonnet 4.6+ (incl. Sonnet 5),
        Haiku 5+ and the always-on Mythos class."""
        mid = model_id or self.model or ""
        if self._is_always_on_thinking(mid) or self._is_haiku_5(mid):
            return True
        version = self._parse_claude_major_minor(mid, ("claude-opus-", "claude-sonnet-"))
        return version is not None and version >= (4, 6)

    def _supports_xhigh_effort(self, model_id=None):
        """'xhigh' thinking effort (between high and max) — Opus 4.7+, Sonnet 5+,
        Haiku 5+ and the always-on Mythos class. Sonnet 4.6 does NOT support it."""
        mid = model_id or self.model or ""
        if self._is_always_on_thinking(mid) or self._is_haiku_5(mid):
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
        if _HAS_MYAGENT_TOOLS and state.get("browser_enabled"):
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
        if _HAS_MYAGENT_TOOLS and _HAS_EXCEL and state.get("excel_enabled"):
            self.excel_enabled.set(True)
        if _HAS_MYAGENT_TOOLS and _HAS_PHYSICAL and state.get("physical_enabled"):
            self.physical_enabled.set(True)
        if state.get("diag_enabled"):
            self.diag_enabled.set(True)
        # `pause_enabled` is the key's name until 2026-10-14 (the Pause box), read once more.
        if state.get("exit_enabled", state.get("pause_enabled")):
            self.exit_enabled.set(True)
        if state.get("dictation_auto_send"):
            self.dictation_auto_send.set(True)
            self._focus_for_reply()      # hands-free from the first message: Mike holds the focus
        # Restore the dialog geometries saved for the CURRENT monitor layout (applied
        # when each dialog next opens). They come from THIS instance's state file, so
        # instance 2 keeps its own placements; a layout with no entry leaves the
        # `_last_*` attributes None and each dialog opens at its default over the main
        # window — the safe fallback when monitors were added, removed or re-scaled.
        for kind, geo in self._dialog_geometries_from_state(state, self._monitor_layout_key()).items():
            setattr(self, f"_last_{kind}_geometry", geo)
        # Restore the Safety dialog's confirm-bypass set.
        self._disabled_confirm_patterns = set(state.get("disabled_confirm_patterns", []))
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
        # Restore window geometry — duo and solo modes are independent, and both are
        # filed PER MONITOR LAYOUT (main_geometries[layout][mode], like the dialogs); a
        # saved position is used only when SOME monitor still shows its title bar, so a
        # main window left on a secondary monitor — negative x when it sits LEFT of the
        # primary — comes back there. The primary-only test used before 2026-09-16
        # (x + w > 0 …) rejected exactly that, which read as "the monitor isn't saved".
        layout_key = self._monitor_layout_key()
        if self._duo_mode:
            # Launched from LaunchSelfBot.bat — restore saved duo geometry or default side-by-side
            duo_geo = self._main_geometry_from_state(state, layout_key, "duo")
            if self._usable_main_geometry(duo_geo):
                self._set_geometry(self.root, duo_geo)
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
                self._set_geometry(self.root, f"{half_w}x{wa_h}+{wa_x + half_w}+{wa_y}")
            else:
                self._set_geometry(self.root, f"{half_w}x{wa_h}+{wa_x}+{wa_y}")
        else:
            # Manual launch — restore the solo geometry saved for THIS monitor layout,
            # else the last used one (another layout's entry is never used); a
            # position no monitor shows gives way to its SIZE centred on the primary.
            geometry = self._main_geometry_from_state(state, layout_key, "solo")
            parsed = self._usable_main_geometry(geometry)
            if parsed:
                w, h, x, y = parsed
                # A manually-opened 2nd instance restores the SAME solo geometry
                # as instance 1 and lands exactly on top of it — which looks like
                # nothing opened (or a crash). If its saved position (nearly)
                # coincides with instance 1's, cascade it down-right so it's a
                # visibly separate window. Cascade only on collision, so manual
                # placement is preserved and the offset can't drift across launches.
                if self._is_second_instance:
                    i1 = None
                    try:
                        with open(APP_STATE_FILE, encoding="utf-8") as f:
                            i1 = self._parse_geometry(
                                self._main_geometry_from_state(json.load(f), layout_key, "solo"))
                    except (OSError, json.JSONDecodeError):
                        pass
                    if i1 is not None and abs(x - i1[2]) < 30 and abs(y - i1[3]) < 30:
                        # Fixed cascade off instance 1 — no clamp against
                        # winfo_screenwidth/height, which report only the PRIMARY
                        # monitor and would yank instance 2 onto it when the pair
                        # lives on a secondary display. A 60 px shift keeps a
                        # window that already passed the on-screen validity check
                        # on the same monitor and still reachable.
                        x, y = x + CASCADE_OFFSET, y + CASCADE_OFFSET
                        geometry = f"{w}x{h}+{x}+{y}"
                self.root.update_idletasks()
                self._set_geometry(self.root, geometry)
            else:
                # No saved position a monitor shows (the laptop undocked): the saved
                # SIZE — the last used, when real — or the default, centred on the
                # primary monitor, never left to the WM (since 2026-10-13); instance 2
                # cascades off that spot so it doesn't stack on instance 1.
                size = self._parse_geometry(geometry)
                w, h = ((size[0], size[1]) if size and size[0] >= MAIN_MIN_W and size[1] >= MAIN_MIN_H
                        else (None, None))
                geometry = self._default_main_geometry(w, h)
                if self._is_second_instance:
                    w, h, x, y = self._parse_geometry(geometry)
                    geometry = f"{w}x{h}+{x + CASCADE_OFFSET}+{y + CASCADE_OFFSET}"
                self.root.update_idletasks()
                self._set_geometry(self.root, geometry)

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
            "excel_enabled": self.excel_enabled.get(),
            "physical_enabled": self.physical_enabled.get(),
            "diag_enabled": self.diag_enabled.get(),
            "exit_enabled": self.exit_enabled.get(),
            "dictation_auto_send": self.dictation_auto_send.get(),
        }
        state["disabled_confirm_patterns"] = sorted(self._disabled_confirm_patterns)
        # Load existing state to preserve the other mode's geometry
        try:
            with open(self._state_file, encoding="utf-8") as f:
                existing = json.load(f)
        except (OSError, json.JSONDecodeError):
            existing = {}
        # Dialog geometries — live value if open, else the last-known one — filed under
        # the CURRENT monitor layout's key; other layouts' entries in the existing file
        # are kept, so a laptop docked and undocked remembers both arrangements. Written
        # to self._state_file (app_state.json / app_state_2.json), so each instance
        # remembers its own dialog placements.
        layout_key = self._monitor_layout_key()
        layouts = existing.get("dialog_geometries")
        layouts = dict(layouts) if isinstance(layouts, dict) else {}
        current = self._current_dialog_geometries()
        if current:
            layouts[layout_key] = current
        if layouts:
            state["dialog_geometries"] = layouts
        # Main window — filed per monitor layout too: main_geometries[layout][mode],
        # mode "solo" / "duo", which is what _load_last_state restores from. Captured
        # only once the root is mapped and at a real size (an unmapped root reports
        # 1x1), so the save in __init__ can't overwrite a good entry with junk. The
        # flat `geometry` / `duo_geometry` keys below stay as last-seen values.
        mains = existing.get("main_geometries")
        mains = ({k: dict(v) for k, v in mains.items() if isinstance(v, dict)}
                 if isinstance(mains, dict) else {})
        main_geo = self.root.geometry()
        parsed = self._parse_geometry(main_geo)
        if (self.root.winfo_ismapped() and parsed
                and parsed[0] >= MAIN_MIN_W and parsed[1] >= MAIN_MIN_H):
            mains.setdefault(layout_key, {})["duo" if self._duo_mode else "solo"] = main_geo
        if mains:
            state["main_geometries"] = mains
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
                # Exists but empty or unreadable — serve a session-only Default
                # rather than overwriting a possibly half-synced store; retry
                # next launch. An UNREADABLE one is marked, so nothing built on
                # the stand-in is ever saved over the file (_save_prompts_to_disk).
                default = {"Default": {"text": DEFAULT_SYSTEM_PROMPT}}
                if _store_unreadable(PROMPTS_FILE):
                    return _UnreadableStore(default)
                return default
            prompts["Default"] = {"text": DEFAULT_SYSTEM_PROMPT}
            migrated = True
        if migrated:
            self._save_prompts_to_disk(prompts)
        return prompts

    def _save_prompts_to_disk(self, prompts):
        """Save the prompt library — True, or False (after saying why) when
        `prompts` is the stand-in served for an unreadable store file: every
        caller modifies the dict it was handed and saves it, so saving the
        stand-in would replace the whole library with Default plus one change
        (and OneDrive would sync that to every machine). MyAgent's
        _save_instructions_to_disk rule; a refusal is returned, not raised,
        because manage_prompts runs on the streaming worker, where an
        exception would end the turn."""
        if isinstance(prompts, _UnreadableStore):
            msg = ("The system prompt store could not be read, so this change was "
                   "NOT saved — saving would have replaced the whole library with "
                   "this session's stand-in. Fix or restore "
                   f"{os.path.basename(PROMPTS_FILE)} (OneDrive keeps its earlier "
                   "versions), then restart SelfBot.")
            self.queue.put({"type": "warning", "content": f"⚠ {msg}\n"})
            if threading.current_thread() is threading.main_thread():
                try:   # an editor action: say so where the user is looking
                    messagebox.showerror("System prompt store unreadable", msg,
                                         parent=getattr(self, "prompt_editor_window", None))
                except tk.TclError:
                    pass
            return False
        _save_store(PROMPTS_FILE, prompts)
        return True

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
            "excel": self.excel_enabled.get(),
            "physical": self.physical_enabled.get(),
            "exit": self.exit_enabled.get(),
            "dictation_auto_send": self.dictation_auto_send.get(),
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
        if "browser" in entry and _HAS_MYAGENT_TOOLS:
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
        if "excel" in entry and _HAS_MYAGENT_TOOLS and _HAS_EXCEL:
            self.excel_enabled.set(bool(entry["excel"]))
        if "physical" in entry and _HAS_MYAGENT_TOOLS and _HAS_PHYSICAL:
            self.physical_enabled.set(bool(entry["physical"]))
        if "exit" in entry:
            self.exit_enabled.set(bool(entry["exit"]))
        elif "pause" in entry:      # the key's name until 2026-10-14 (the Pause box), honoured until the prompt is re-saved
            self.exit_enabled.set(bool(entry["pause"]))
        if "dictation_auto_send" in entry:
            self.dictation_auto_send.set(bool(entry["dictation_auto_send"]))
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
        win.withdraw()  # Hide until geometry is set (avoids a flash at the wrong spot)
        win.title("System Prompt Editor")
        if IS_WINDOWS:
            win.transient(self.root)
        self.prompt_editor_window = win

        def _on_prompt_editor_close():
            self._last_prompt_editor_geometry = win.geometry()
            self._save_last_state()
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_prompt_editor_close)

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

        # Restore geometry AFTER content is laid out, then show (withdraw/deiconify) —
        # the same pattern as the Skills Manager and Safety dialogs.
        win.update_idletasks()
        self._place_dialog(win, "prompt_editor", (650, 500))
        win.deiconify()

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
        if self._save_prompts_to_disk(prompts) is False:
            return
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
        # GUI-only guard (2026-09-02): a prompt is a whole saved environment
        # (text + model params + tool toggles + skill modes + safety bypasses)
        # in the OneDrive-shared store, gone from every machine with no undo.
        # The manage_prompts tool's delete action is deliberately NOT gated —
        # a tool delete is the model acting on an explicit instruction.
        if not messagebox.askyesno(
                "Delete system prompt",
                f"Permanently delete the saved system prompt '{name}'?\n\n"
                "Its text and bundled environment are removed from the shared store "
                "on every synced machine. This cannot be undone.",
                icon="warning", default="no", parent=self.prompt_editor_window):
            return
        prompts.pop(name)
        if self._save_prompts_to_disk(prompts) is False:
            return
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
        # Apply closes the editor too, so capture its geometry before the destroy —
        # otherwise only an [X] close would remember where it was.
        self._last_prompt_editor_geometry = self.prompt_editor_window.geometry()
        self._save_last_state()
        self.prompt_editor_window.destroy()

    # --- Skills System ---

    def _load_skills(self):
        # Per-skill SKILL.md tree (frontmatter: name/description/mode). Runs
        # the one-shot skills.json→tree migration and heals OneDrive per-file
        # conflict forks; every entry comes back with a valid mode. The modes
        # read here are the GLOBAL ones, kept apart from the session modes a
        # system prompt restores into self.skills (see _skills_for_tree).
        skills = _load_skills_tree(SKILLS_DIR)
        self._skill_global_modes = {n: e.get("mode", "disabled")
                                    for n, e in skills.items() if isinstance(e, dict)}
        return skills

    def _save_skills(self):
        # Diff-aware and WRITE-ONLY (never deletes folders) — deletion is an
        # explicit action via _delete_skill_tree_entry at the delete callsites.
        _save_skills_tree(SKILLS_DIR, self._skills_for_tree())

    def _skills_for_tree(self):
        """self.skills as the tree should record it: each skill with its GLOBAL
        mode. A system prompt's skill modes are session-only
        (_restore_skill_modes writes them into self.skills), so a save must
        not carry them into the shared tree — it used to: any later skill save
        wrote every skill's session mode as its global one. A mode reaches the
        tree only through _set_skill_mode (Cycle Mode, manage_skills
        create / update); a skill the map has not seen yet (a new one) is
        recorded with its own mode. A host that never loaded the tree (a bare
        test host) has no map and writes self.skills as it stands. An in-file
        copy of SkillsMixin's."""
        glob = getattr(self, "_skill_global_modes", None)
        if glob is None:
            return self.skills
        out = {}
        for name, entry in self.skills.items():
            if isinstance(entry, dict):
                mode = glob.setdefault(name, entry.get("mode", "disabled"))
                if entry.get("mode") != mode:
                    entry = dict(entry, mode=mode)
            out[name] = entry
        return out

    def _set_skill_mode(self, name, mode):
        """An explicit mode change (Cycle Mode, manage_skills): the session
        AND the global mode, so the next save writes it to the tree."""
        self.skills[name]["mode"] = mode
        glob = getattr(self, "_skill_global_modes", None)
        if glob is not None:
            glob[name] = mode

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

    # ── Dialog geometry persistence, per monitor layout ─────────────────────

    def _monitor_rects(self):
        """Every monitor's (left, top, right, bottom), the primary screen alone when
        the enumeration fails — the one source for every check below."""
        return self._get_display_rects() or [
            (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())]

    def _monitor_layout_key(self):
        """A string naming the current monitor arrangement — every monitor's rect,
        sorted — under which the dialog geometries are filed. A monitor added,
        removed, moved or re-scaled changes the key, so the geometries saved on
        one arrangement are never applied to another (MyAgent's rule)."""
        return "|".join(",".join(str(v) for v in rect) for rect in sorted(self._monitor_rects()))

    @staticmethod
    def _parse_geometry(geo):
        """(w, h, x, y) from a Tk 'WxH+X+Y' string, else None."""
        m = re.match(GEOMETRY_RE, geo or "")
        return tuple(int(g) for g in m.groups()) if m else None

    @staticmethod
    def _main_geometry_from_state(state, layout_key, mode):
        """The main-window geometry to restore for `mode` ("solo" / "duo"): the
        entry saved under `layout_key` in `main_geometries`, else the flat
        `geometry` / `duo_geometry` key — the LAST USED position, which every
        save still writes and all a pre-2026-09-16 file has (since 2026-10-13;
        until then a file with per-layout data read it for no layout, so a
        layout seen for the first time — the laptop undocked — lost the
        window's size and spot). Validated against the monitors by the
        caller, so a last position on a monitor that is gone is refused there.
        Another layout's entry is never used."""
        layouts = state.get("main_geometries")
        if isinstance(layouts, dict):
            entry = layouts.get(layout_key)
            geo = (entry.get(mode) or None) if isinstance(entry, dict) else None
            if geo:
                return geo
        return state.get("duo_geometry" if mode == "duo" else "geometry") or None

    def _usable_main_geometry(self, geo):
        """(w, h, x, y) when `geo` is a real-size main-window geometry whose title
        bar SOME monitor shows, else None — the any-monitor test that replaced the
        primary-only one."""
        parsed = self._parse_geometry(geo)
        if not parsed:
            return None
        w, h, x, y = parsed
        if w < MAIN_MIN_W or h < MAIN_MIN_H or not self._dialog_geometry_visible(x, y, w, h):
            return None
        return parsed

    def _default_main_geometry(self, w=None, h=None):
        """`w`x`h` — DEFAULT_GEOMETRY's size without them — shrunk to fit the
        primary monitor and centred on it: the main window's spot when no saved
        position is usable (since 2026-10-13, the rule MyAgent / MyBackup /
        CSVEditor / TodoList share; a size-only geometry left the spot to the
        WM, and the saved size went with it)."""
        m = re.match(r"(\d+)x(\d+)", DEFAULT_GEOMETRY)
        w = w or int(m.group(1))
        h = h or int(m.group(2))
        left, top, right, bottom = self._monitor_rect_containing(0, 0)
        w, h = min(w, right - left), min(h, bottom - top)
        return f"{w}x{h}+{left + (right - left - w) // 2}+{top + (bottom - top - h) // 2}"

    @staticmethod
    def _dialog_geometries_from_state(state, layout_key):
        """The dialog geometries to restore from a loaded state dict: the entry
        saved under `layout_key`, else — for a pre-2026-09-16 file — the legacy
        flat keys, adopted once (they are validated against the monitors at open).
        Another layout's entry is never used."""
        layouts = state.get("dialog_geometries")
        entry = layouts.get(layout_key) if isinstance(layouts, dict) else None
        if isinstance(entry, dict):
            return {k: v for k, v in entry.items() if k in DIALOG_WINDOWS and v}
        if layouts:      # per-layout data exists, just not for this arrangement
            return {}
        return {kind: state[key] for kind, key in DIALOG_LEGACY_KEYS.items() if state.get(key)}

    def _current_dialog_geometries(self):
        """kind → geometry for every persisted dialog: live while open, else the
        last-known value; dialogs never opened are absent."""
        geos = {}
        for kind, win_attr in DIALOG_WINDOWS.items():
            win = getattr(self, win_attr, None)
            if win is not None and win.winfo_exists():
                geos[kind] = win.geometry()
            elif getattr(self, f"_last_{kind}_geometry", None):
                geos[kind] = getattr(self, f"_last_{kind}_geometry")
        return geos

    def _monitor_rect_containing(self, x, y):
        """The monitor whose rect holds the point, else the first (primary)."""
        rects = self._monitor_rects()
        for left, top, right, bottom in rects:
            if left <= x < right and top <= y < bottom:
                return (left, top, right, bottom)
        return rects[0]

    def _default_dialog_geometry(self, w, h):
        """A `w`x`h` dialog centred on the main window, shrunk to fit and clamped
        onto the monitor holding the main window's centre — so a dialog opened
        with no usable saved position lands where the user is looking, whatever
        the monitor count or resolution."""
        rx, ry = self.root.winfo_x(), self.root.winfo_y()
        rw, rh = self.root.winfo_width(), self.root.winfo_height()
        left, top, right, bottom = self._monitor_rect_containing(rx + rw // 2, ry + rh // 2)
        w, h = min(w, right - left), min(h, bottom - top)
        x = max(left, min(rx + (rw - w) // 2, right - w))
        y = max(top, min(ry + (rh - h) // 2, bottom - h))
        return f"{w}x{h}+{x}+{y}"

    # ── Setting a geometry: the one way, every platform ────────────────

    def _set_geometry(self, win, geo):
        """The ONE way a persisted window's geometry is set — `_place_window`,
        the main-window restore, the monitor reclaim and PS Safety's re-apply
        all come through here — because on Windows a plain `win.geometry(geo)`
        does not give the size it names on a monitor whose scale differs from
        the primary's.

        Tk 8.6's Windows port sizes the frame around a window from the PRIMARY
        monitor's border metrics, while Windows (the process is per-monitor
        DPI aware) draws the frame at the monitor's own scale. Measured
        2026-10-02 on the laptop — a 150 % panel with a 100 % screen beside
        it: a window asked to be 613x946 on the 100 % screen maps as 619x963,
        the two frames' difference (6 px of width, 17 of height); Tk then
        re-measures the frame's HEIGHT from the mapped window but never its
        WIDTH, so every later width request on that screen comes out 6 px
        wide for the window's life. The app saves what Tk reports, so each
        launch grew the main window and every dialog by one frame difference
        — the "moves right a little each time it is reopened" report.

        So: the geometry is applied, and once the window is shown in the
        normal state (at once if it already is; else at its first <Map>, or
        for a maximized start at the first <Configure> after it returns to
        normal) `_settle_geometry` re-applies it and corrects the residual.
        A size-less `geo` ('' or '+x+y') is applied as is and cancels a
        pending correction — Model Setup gives its size back to Tk that way.
        Positions are exact on every platform and are never adjusted; off
        Windows this is `win.geometry(geo)`."""
        if not IS_WINDOWS or self._parse_geometry(geo) is None:
            win.geometry(geo)
            win._geometry_intent = None
            return
        if not getattr(win, "_geometry_settle_bound", False):
            win._geometry_settle_bound = True
            for seq in ("<Map>", "<Configure>"):
                win.bind(seq, lambda e, w=win: self._settle_when_shown(w, e), add="+")
        if self._shown_normal(win):
            win._geometry_intent = None
            self._settle_geometry(win, geo)
        else:
            win.geometry(geo)
            win._geometry_intent = geo

    @staticmethod
    def _shown_normal(win):
        """True while `win` is mapped in the normal state — the only state in
        which a size request takes effect at once (Tk parks one made while
        zoomed or iconic until the window is normal again)."""
        try:
            return bool(win.winfo_exists() and win.winfo_ismapped() and win.state() == "normal")
        except tk.TclError:
            return False

    def _settle_when_shown(self, win, event=None):
        """The <Map> / <Configure> handler: run the pending correction once the
        window is shown normal. Both bindings fire for every descendant too (a
        toplevel's path is in its children's bindtags), hence the identity
        check; the intent is cleared BEFORE the settle, whose own <Configure>s
        then find nothing to do — and a later move or resize by the user is
        never undone, because nothing is pending any more."""
        if event is not None and str(event.widget) != str(win):
            return
        geo = getattr(win, "_geometry_intent", None)
        if not geo or not self._shown_normal(win):
            return
        win._geometry_intent = None
        self._settle_geometry(win, geo)

    def _settle_geometry(self, win, geo):
        """Apply `geo` to a SHOWN window and make its size come out as asked:
        read the size Tk reports after each request and ask again with the
        residual taken off, at most GEOMETRY_SETTLE_PASSES times (two on the
        mixed-scale laptop, one wherever Tk gets it right). Pure Tk — no
        Win32 metrics — so it corrects whatever the frame difference is, in
        either direction, and an idle flush is enough for the read-back
        because Windows applies the request synchronously. A residual beyond
        GEOMETRY_SETTLE_MAX_DELTA is no frame difference (a minsize, a
        monitor edge) and is left alone rather than chased."""
        target = self._parse_geometry(geo)
        if target is None:
            return
        w, h, x, y = target
        ask_w, ask_h = w, h
        for _ in range(GEOMETRY_SETTLE_PASSES):
            try:
                win.geometry(f"{ask_w}x{ask_h}+{x}+{y}")
                win.update_idletasks()
                got = self._parse_geometry(win.geometry())
            except tk.TclError:
                return
            if got is None:
                return
            dw, dh = got[0] - w, got[1] - h
            if (dw, dh) == (0, 0) or max(abs(dw), abs(dh)) > GEOMETRY_SETTLE_MAX_DELTA:
                return
            ask_w, ask_h = max(1, ask_w - dw), max(1, ask_h - dh)

    def _place_dialog(self, win, kind, default_size, min_size=(400, 300)):
        """Apply a dialog's geometry before it is shown and return it: the position
        saved for this monitor layout when some monitor still shows it, else that
        saved SIZE at the default position; with nothing saved, the default size —
        both defaults centred on the main window (`_default_dialog_geometry`)."""
        saved = getattr(self, f"_last_{kind}_geometry", None)
        geo = self._sanitize_geometry(saved, *min_size) if saved else None
        if not geo or "+" not in geo:
            m = re.match(r"(\d+)x(\d+)", geo or "")
            w, h = (int(m.group(1)), int(m.group(2))) if m else default_size
            geo = self._default_dialog_geometry(w, h)
        self._set_geometry(win, geo)
        return geo

    def _dialog_geometry_visible(self, x, y, w, h):
        """True when enough of the window's title bar lies on SOME monitor to grab
        it: at least 50 px of the 30-px title strip, 10 px tall (MyAgent's rule).
        Checked per monitor via _get_display_rects(), falling back to the primary
        screen when the enumeration fails. The earlier primary-only test
        (x < screen_width and x + w > 0) rejected every position on a monitor
        LEFT of the primary, where x is negative — so a dialog parked there came
        back wherever the WM chose, which is what made the Skills Manager and
        Safety dialogs look non-persistent (2026-09-16)."""
        for left, top, right, bottom in self._monitor_rects():
            overlap_w = min(x + w, right) - max(x, left)
            overlap_h = min(y + 30, bottom) - max(y, top)
            if overlap_w >= 50 and overlap_h >= 10:
                return True
        return False

    def _sanitize_geometry(self, geo, min_w=400, min_h=300):
        """Validate a saved 'WxH+X+Y' geometry: enforce a minimum size and drop a
        position no monitor shows (letting the WM place it). The position test is
        per monitor — see _dialog_geometry_visible."""
        m = re.match(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geo or "")
        if not m:
            return f"{max(min_w, 900)}x{max(min_h, 500)}"
        w, h, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        w, h = max(w, min_w), max(h, min_h)
        if self._dialog_geometry_visible(x, y, w, h):
            return f"{w}x{h}+{x}+{y}"
        return f"{w}x{h}"

    # ── Toolbar "last pressed" highlight (in-file copy of ui_mixin's) ──────

    def _track_toolbar_presses(self, *buttons_and_commands):
        """Make the given (button, command) pairs a "last pressed" group.

        Nothing is repainted here, so at startup every button keeps the look it
        was created with. Pressing one paints it TOOLBAR_ACTIVE_BG with a bold
        face and puts each other button back to its resting look: the
        creation-time background and its own font at regular weight. Only the
        weight changes — both twins are derived from the button's own font
        (never a hardcoded face), so each button keeps whatever family and
        size it was created with. The wrapper repaints BEFORE running the
        command, so a command that opens a dialog can't delay it.
        """
        self._toolbar_styles = {}
        for button, command in buttons_and_commands:
            rest_font = tkfont.Font(root=button, font=button.cget("font"))
            rest_font.configure(weight="normal")
            bold_font = tkfont.Font(root=button, font=button.cget("font"))
            bold_font.configure(weight="bold")
            self._toolbar_styles[button] = {
                "rest": {
                    "bg": button.cget("bg"),
                    "activebackground": button.cget("activebackground"),
                    # macOS Aqua ignores bg on the button face; the highlight
                    # frame around it is the one part that does take a colour.
                    "highlightbackground": button.cget("highlightbackground"),
                    "font": rest_font,
                },
                "active": {
                    "bg": TOOLBAR_ACTIVE_BG,
                    "activebackground": TOOLBAR_ACTIVE_BG,
                    "highlightbackground": TOOLBAR_ACTIVE_BG,
                    "font": bold_font,
                },
            }
            button.config(
                command=lambda b=button, c=command: self._press_toolbar_button(b, c))

    def _press_toolbar_button(self, button, command):
        """Repaint the group for a press of `button`, then run its command."""
        for other, styles in self._toolbar_styles.items():
            other.config(**styles["active" if other is button else "rest"])
        command()

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
        """The system prompt a request sends. While a stream_worker runs (a
        MyAgent run, a SelfBot reply) it is the text frozen at its start
        (_freeze_system_prompt), so a change to what the prompt is built
        from — the base prompt, a skill's text, description or mode, a skill
        created or deleted — reaches the NEXT run, never the calls in between.
        The system prompt heads the cached prefix, so a change mid-run made
        the next call re-write the whole prompt cache and, on the models that
        bind thinking blocks to that prefix (Fable 5.1, Mythos 5.1, Opus 5.5),
        drop every earlier thinking block. get_skill reads the live skills,
        so an edited skill's text is there at once."""
        frozen = getattr(self, "_run_system_prompt", None)
        if frozen is not None:
            return frozen
        return self._compose_system_prompt()

    def _compose_system_prompt(self):
        parts = [self.system_prompt]
        for name, skill in self.skills.items():
            if skill.get("mode") == "enabled":
                parts.append(f"## Skill: {name}\n{skill['content']}")
        od_block = self._format_on_demand_listing(self.skills)
        if od_block:
            parts.append(od_block)
        return "\n\n".join(parts)

    def _freeze_system_prompt(self):
        """Fix the system prompt for the stream_worker that is starting."""
        self._run_system_prompt = self._compose_system_prompt()

    def _thaw_system_prompt(self):
        """Let it go when that stream_worker ends: the prompt is composed from
        the live skills again."""
        self._run_system_prompt = None

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

        # The skill whose content the editor currently shows (set by the list
        # selection, cleared by NEW). SAVE under a DIFFERENT, NEW name is a
        # save-as: the loaded skill is its source, and the source's bundled
        # resource files (references/, scripts/, …) are copied into the new
        # folder — the folder is the skill, so a copy that carried only
        # SKILL.md was not a copy of the skill (mirrors MyAgent, 2026-09-25).
        loaded = {"name": None}

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
            is_new = name not in self.skills
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
            # Save-as: a NEW name while another skill's content is loaded
            # copies the source's bundled resource files into the new folder
            # (never onto an EXISTING skill, whose folder owns its own files).
            src = loaded["name"]
            if is_new and src and src != name and src in self.skills:
                copied = _copy_skill_resources(SKILLS_DIR, src, name)
                if copied:
                    messagebox.showinfo(
                        "Bundled files copied",
                        f"Copied {len(copied)} bundled resource file(s) from "
                        f"'{src}' into the new skill's folder.", parent=win)
            loaded["name"] = name
            refresh_list()
            self._update_skills_button()

        def delete_skill():
            sel = skill_listbox.curselection()
            if not sel:
                messagebox.showwarning("No selection", "Select a skill to delete.", parent=win)
                return
            name = skill_listbox.get(sel[0])[5:]
            if name in self.skills:
                # GUI-only guard (2026-09-02): DELETE removes the skill's whole
                # folder (SKILL.md plus any bundled files) from the OneDrive-
                # shared tree on every machine, with no undo. The manage_skills
                # tool's delete action is deliberately NOT gated — a tool delete
                # is the model acting on an explicit instruction.
                if not messagebox.askyesno(
                        "Delete skill",
                        f"Permanently delete the skill '{name}'?\n\n"
                        "Its folder (SKILL.md and any bundled files) is removed from the "
                        "shared skills tree on every synced machine. This cannot be undone.",
                        icon="warning", default="no", parent=win):
                    return
                del self.skills[name]
                (getattr(self, "_skill_global_modes", None) or {}).pop(name, None)
                _delete_skill_tree_entry(SKILLS_DIR, name)  # _save_skills never deletes
                self._save_skills()
                refresh_list()
                name_entry.delete(0, tk.END)
                desc_entry.delete("1.0", tk.END)
                text_editor.delete("1.0", tk.END)
                if loaded["name"] == name:
                    loaded["name"] = None   # its fields were just cleared too
                self._update_skills_button()

        def new_skill():
            name_entry.delete(0, tk.END)
            desc_entry.delete("1.0", tk.END)
            text_editor.delete("1.0", tk.END)
            skill_listbox.selection_clear(0, tk.END)
            loaded["name"] = None   # a fresh skill has no save-as source

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

        # Left panel: Cycle Mode button ABOVE the listbox, the SKILLS band
        # between them (2026-09-12, the band MyAgent's two lists wear —
        # list_title_band is an in-file copy of ui_mixin's): the list proper's
        # width, not the scrollbar's, which runs the full height beside band
        # and list
        left = tk.Frame(win)
        left.grid(row=2, column=0, sticky="nsew", padx=(10, 5), pady=(0, 10))
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)

        toggle_btn = tk.Button(left, text="Cycle Mode", font=("Arial", 9))
        toggle_btn.grid(row=0, column=0, sticky="w", pady=(0, 5))

        skills_band = list_title_band(left, "SKILLS")
        skills_band.grid(row=1, column=0, sticky="ew")
        skill_listbox = tk.Listbox(left, font=("Arial", 10), width=40)
        skill_listbox.grid(row=2, column=0, sticky="nsew")
        list_scrollbar = tk.Scrollbar(left, command=skill_listbox.yview)
        list_scrollbar.grid(row=1, column=1, rowspan=2, sticky="ns")
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
                loaded["name"] = name

        def toggle_skill():
            sel = skill_listbox.curselection()
            if not sel:
                messagebox.showwarning("No selection", "Select a skill to toggle.", parent=win)
                return
            name = skill_listbox.get(sel[0])[5:]
            if name in self.skills:
                cycle = {"disabled": "enabled", "enabled": "on_demand", "on_demand": "disabled"}
                cur = self.skills[name].get("mode", "disabled")
                self._set_skill_mode(name, cycle.get(cur, "disabled"))
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
        self._place_dialog(win, "skills_dialog", (900, 500))
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
        # GUI-only guard (2026-09-02): a click on DELETE removes the chat's
        # .json AND its .txt transcript from disk with no undo. No tool path
        # reaches this handler, so nothing model-driven is affected.
        if not messagebox.askyesno(
                "Delete chat",
                f"Permanently delete the saved chat '{name}'?\n\n"
                "Its .json file and .txt transcript are removed from saved_chats/. "
                "This cannot be undone.",
                icon="warning", default="no", parent=self.root):
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
        self._thinking_drops_noted = None  # a fresh conversation: the notice may be said again
        self._file_read_paths = set()      # and the file tools' read-before-edit memory starts over
        self._held_for_partner = False     # a message in the box is a plain draft again
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
        # A dictation in flight (the Mike button, 2026-10-14): Enter while
        # recording ENDS the recording — Mike's own toggle — and while
        # transcribing is swallowed, because sending mid-dictation would lose
        # the speech (the Agent Request dialog's rule in MyAgent).
        dictation = getattr(self, "_dictation", None)
        if dictation is not None and dictation.state == "recording":
            dictation.toggle()
            return "break"
        if dictation is not None and dictation.state == "transcribing":
            return "break"
        self._send_as_enter()
        return "break"

    def _send_as_enter(self):
        """What Enter does once the Shift and dictation rules have passed — and
        what the Auto-send box does the moment a transcript lands (the row's
        `send`): the duo's send delay, which parks the message for editing or
        cancellation, else the send itself. Not parked while the partner is
        still answering: send_message then keeps it in the box, to go in with
        that answer."""
        if self._send_delay > 0 and not self._awaiting_partner():
            # Delay before processing to allow editing/cancellation
            self.input_field.config(state="disabled")
            self._send_timer = self.root.after(self._send_delay, self._delayed_send)
        else:
            self.send_message()

    def _delayed_send(self):
        self.input_field.config(state="normal")
        self.send_message()

    def send_message(self, from_peer=False):
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

        # A message typed while the partner is still answering waits in the box
        # and goes in with that answer (_poll_auto_msg) — the user's choice,
        # 2026-10-14: sent now, it crossed that answer in the shared message
        # file and one of the two was lost.
        if not from_peer and self._awaiting_partner():
            if not self._held_for_partner:
                self._held_for_partner = True
                self._chat_insert((
                    "⏸ Waiting for the other window's answer — your message stays "
                    "in the box and is sent with it.\n", "tool_info"))
            return
        self._held_for_partner = False

        # The conversation's opener, for the partner window (_write_inject_file)
        # — only one typed HERE, in either instance. When the partner opened
        # the conversation, its reply arrives as this window's first message
        # (from_peer, _poll_auto_msg); the partner shows that text already,
        # and handing it back made the partner show its own reply twice.
        if self._response_count == 0 and not from_peer:
            self._first_message_text = user_text

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
        if self._auto_chat.get():
            self._set_busy(True)   # the partner waits for this answer

        thread = threading.Thread(
            target=self.stream_worker, args=(list(self.messages),), daemon=True
        )
        thread.start()

    def _check_command_safety(self, command):
        """Check command against safety tiers. Returns (allowed, message)."""
        for pattern in COMMAND_BLOCKED:
            if re.search(pattern, command, re.IGNORECASE):
                return "blocked", f"BLOCKED: Command matches dangerous pattern ({pattern})"

        # Every confirm pattern is checked, not only the first that matches:
        # unticking a pattern in the Safety dialog bypasses THAT pattern, never
        # a still-ticked one the same command also matches ("Remove-Item x
        # -Recurse" with Remove-Item unticked still confirms on -Recurse).
        # Skipped only when every matching pattern is unticked — MyAgent's rule.
        bypassed = None
        for pattern in COMMAND_CONFIRM:
            if re.search(pattern, command, re.IGNORECASE):
                if pattern not in self._disabled_confirm_patterns:
                    return "confirm", pattern
                if bypassed is None:
                    bypassed = pattern
        if bypassed is not None:
            return "skipped", bypassed  # bypassed via the Safety dialog
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
        geo = self._place_dialog(dlg, "ps_safety", (560, 760))
        dlg.deiconify()
        dlg.after(100, lambda: self._set_geometry(dlg, geo) if dlg.winfo_exists() else None)

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
            self._set_geometry(dlg, f"{w}x{h}+{x}+{y}")

        # Schedule the dialog on the main thread. The wait is clocked out of
        # the reply's working time (helpers.input_wait_timer), as every
        # dialog wait is in MyAgent, so the cost log's TIME field measures
        # the model working, not the user's response latency.
        self.root.after(0, ask)
        with input_wait_timer(self):
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

    @staticmethod
    def _run_command_env(environ=None, executable=None):
        """The environment a run_command child inherits: the caller's, with
        the interpreter's own bin folder FIRST on PATH (and VIRTUAL_ENV set)
        when MyAgent runs from a venv — what `activate` would have done.
        The launchers start `.venv/bin/python MyAgent.py` without activating
        anything, so a child's bare `python3` / `pip` resolved to whatever the
        system has — on the Mac the Xcode Command Line Tools' Python 3.9, with
        none of the venv's packages (the TCC log of 2026-10-06 showed the
        model's scripts running under it) — while every instruction and skill
        assumes the venv's. A venv is recognised by its `pyvenv.cfg` beside
        the bin folder, Python's own rule; outside one the environment comes
        back unchanged. Pure: the mapping and the interpreter are parameters
        so a test can pass its own."""
        env = dict(os.environ if environ is None else environ)
        exe = sys.executable if executable is None else executable
        if not exe:
            return env
        bin_dir = os.path.dirname(os.path.abspath(exe))
        venv_dir = os.path.dirname(bin_dir)
        if not os.path.isfile(os.path.join(venv_dir, "pyvenv.cfg")):
            return env
        same = os.path.normcase(bin_dir)
        rest = [p for p in env.get("PATH", "").split(os.pathsep)
                if p and os.path.normcase(p) != same]
        env["PATH"] = os.pathsep.join([bin_dir] + rest)
        env["VIRTUAL_ENV"] = venv_dir
        return env

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
            # UTF-8 output from PowerShell, decoded as UTF-8 with replacement —
            # see _PS_UTF8_OUTPUT (the bare command used to come back garbled or,
            # on one undecodable byte, as no output at all).
            shell_cmd = (["powershell", "-NoProfile", "-Command",
                          _PS_UTF8_OUTPUT + "\n" + command]
                         if IS_WINDOWS else ["/bin/bash", "-c", command])
            # Popen, not subprocess.run: on timeout the whole process TREE must
            # die before the final pipe read, and POSIX needs its own session
            # (start_new_session) for killpg to reach every descendant. On
            # Windows the child gets NO console window (_SUBPROCESS_NOWND, as
            # MyAgent passes it since 2026-09-03): without the flag every
            # run_command flashed a terminal over the chat (the user saw it
            # while dictating, 2026-10-14 - the bot was reading the clock).
            popen_kwargs = dict(_SUBPROCESS_NOWND)
            if not IS_WINDOWS:
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen(
                shell_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                # The venv's bin folder first on PATH, as MyAgent's children
                # get it (an in-file copy of SafetyMixin._run_command_env, the
                # 2026-10-06 fix): a bare `python3` / `pip` is this venv's.
                env=self._run_command_env(),
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

    def _write_inject_file(self):
        """Hand the opener typed in this window to the partner window, which
        shows it above the first reply it receives: the partner's own
        conversation starts with this window's reply, so the human's opening
        line would never appear there. Either instance — the human may open
        the conversation in either window. Written once, with the
        conversation's first reply and just ahead of it (check_queue's
        `complete` writes the reply 1 s later; the partner polls both files
        every 0.5 s). Nothing to write when the partner opened the
        conversation (send_message captures no opener then)."""
        text, self._first_message_text = self._first_message_text, ""
        if not text:
            return
        try:
            data = {
                "from_pid": self._my_pid,
                "label": self._get_user_label(),
                "text": text,
            }
            with open(INJECT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

    def _poll_inject_file(self):
        """Poll for the partner window's opener (_write_inject_file) and show
        it at the top of this window's conversation. A file this instance
        wrote is the partner's to take and is left alone. The opener is taken
        once and shown only into a conversation not yet started — once this
        window has messages of its own it was typed here, or is stale.
        Polling ends at the first reply this window completes: an opener
        always arrives before it, or not at all."""
        if getattr(self, '_closing', False) or self._response_count:
            return
        if os.path.exists(INJECT_FILE):
            try:
                with open(INJECT_FILE, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                data = None  # mid-write, or taken meanwhile: look again
            if isinstance(data, dict) and data.get("from_pid") != self._my_pid:
                try:
                    os.remove(INJECT_FILE)
                except OSError:
                    pass
                label = data.get("label", "You")
                text = data.get("text", "")
                if text and not self.messages:
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, f"{label}: ", "assistant_label")
                    self.chat_display.insert(tk.END, text + "\n\n", "assistant")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                return
        self.root.after(500, self._poll_inject_file)

    def _toggle_auto_chat(self):
        """Toggle the auto-chat loop on/off."""
        on = not self._auto_chat.get()
        self._auto_chat.set(on)
        self._auto_chat_user_off = not on
        if on:
            self._auto_chat_btn.config(text="Auto: ON", bg="#2e7d32", fg="white")
            self._send_delay = self._delay_seconds * 1000
        else:
            self._auto_chat_btn.config(text="Auto: OFF", bg="#c62828", fg="white")
            self._send_delay = 0
        if on and self._pending_injection and not self.streaming:
            self._pending_injection = False
            self._pass_pending = True
            self._set_busy(True)           # the partner waits for it until it is handed over
            if self._response_count == 1:
                self._write_inject_file()  # the held reply is the first: its opener goes first
            self.root.after(1000, self._inject_response_to_other)

    def do_exit_conversation(self, reason=""):
        """Close this instance — the model-callable analog of pressing [X].

        The duo's exit that IS a shutdown (2026-10-14; it replaced the pause /
        rest tool, which switched auto-chat off and left both windows open).
        Runs on the streaming worker thread, so the close is handed to the Tk
        loop: `_on_close` is the [X] handler itself — it latches `_closing`
        (the peer poll and the auto-message poll stop at once, so no new
        message can start a reply), switches auto-chat off, removes the shared
        message file, and WAITS for `self.streaming` to clear before
        `_finish_close` saves the chat, logs the session's cost line, removes
        the in-progress record, closes the peer (WM_CLOSE / SIGTERM — its own
        graceful close) and destroys the window. The wait is what gives the
        model its closing note: the tool result goes back to it, the final API
        round streams into the window, and the `complete` event — `streaming =
        False` — lets the close proceed; auto-chat being off by then, that
        closing reply is never injected into the peer (the chat is over for
        both). The ⏻ notice is queued ahead of the model's final text, so it
        lands first.
        """
        self.root.after(0, self._on_close)
        reason = str(reason or "").strip()
        if reason and reason[-1] not in ".!?…":
            reason += "."
        note = f" — {reason}" if reason else "."
        self.queue.put({
            "type": "warning",
            "content": f"⏻ Conversation ended by the model{note} This instance closes "
                       "when this reply ends — the chat is saved and the cost logged "
                       "first; a connected partner closes with it.\n",
        })
        return (
            "Closing — this instance (and its partner, if one is connected) closes "
            "when this turn ends; the chat is saved first. Anything you write now "
            "is a closing note kept in the saved chat, not delivered to your "
            "partner. End your turn to let the close proceed."
        )

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
        # Saved now, not at the next periodic save: the second window follows
        # this spinner through this window's state file (_sync_delay_from_first).
        try:
            self._save_last_state()
        except Exception:
            pass

    def _sync_delay_from_first(self):
        """Instance 2: follow the first window's Delay(s) spinner, the one pause
        that paces both windows — each turn waits for the RECEIVING window's
        pause, and the second window has no spinner of its own, so until
        2026-10-14 every other turn ran on whatever its own state file held.
        Read from the first window's state file, which saves the spinner at
        once; a file that cannot be read (absent, half-written) keeps the
        current pause."""
        try:
            with open(APP_STATE_FILE, encoding="utf-8") as f:
                val = int(json.load(f)["delay_seconds"])
        except (OSError, ValueError, TypeError, KeyError):
            return
        val = max(0, min(30, val))
        self._delay_seconds = val
        if self._send_delay > 0:
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
        if has_peer and self._is_second_instance:
            self._sync_delay_from_first()   # before the pairing below reads it
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

    # ── Duo turn-taking (2026-10-14) ──────────────────────────────────────
    # One message is in flight at a time. A window is BUSY (its mark file) from
    # taking a message until its answer is handed over; a message the human
    # sends meanwhile in the other window stays in that box and goes in with
    # the answer — sent at once, it crossed the answer in the one shared
    # message file and one of the two was lost.

    def _busy_file(self, partner=False):
        """This window's busy mark — or, with `partner`, the other window's."""
        mine = 2 if self._is_second_instance else 1
        return f"{DUO_BUSY_PREFIX}{3 - mine if partner else mine}.flag"

    def _set_busy(self, on):
        """Set or clear this window's busy mark: set from taking a message
        whose answer the partner waits for until that answer is handed over
        (_inject_response_to_other), cleared too when a turn ends with nothing
        to hand over, at close, and at launch (a killed predecessor's)."""
        path = self._busy_file()
        try:
            if on:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(str(self._my_pid))
            else:
                os.remove(path)
        except OSError:
            pass

    def _awaiting_partner(self):
        """True while this window waits for its partner's answer, so a message
        typed here must wait for it (send_message): paired, and the partner is
        answering (its busy mark), or a message is in the shared file (the
        partner's answer for us, or ours on its way to it), or this window's
        own reply is about to be handed over (the 1 s before
        _inject_response_to_other). Never in a solo window — nor when the
        partner's turn ended with nothing to hand over, so nothing waits on a
        reply that will not come."""
        auto = getattr(self, "_auto_chat", None)
        if auto is None or not auto.get():
            return False
        if getattr(self, "_pass_pending", False):
            return True
        if os.path.exists(self._busy_file(partner=True)):
            return True
        try:
            with open(AUTO_MSG_FILE, encoding="utf-8") as f:
                return bool(str(json.load(f).get("text", "")).strip())
        except (OSError, ValueError, AttributeError):
            return False

    def _restore_draft(self):
        """Put a draft _poll_auto_msg set aside back in the message box, once
        the arriving message has been sent."""
        draft, self._stashed_draft = self._stashed_draft, ""
        if draft:
            self.input_field.insert("1.0", draft)

    def _inject_response_to_other(self):
        """After a reply completes, write response to shared file for the other instance to pick up."""
        self._pending_injection = False
        self._pass_pending = False
        text = self._current_response_text.strip()
        if text:
            try:
                payload = {"from_pid": self._my_pid, "text": text}
                thinking = self._current_thinking_text.strip()
                if thinking:
                    payload["thinking"] = thinking
                with open(AUTO_MSG_FILE, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
            except OSError:
                pass
        # Handed over (the message is in the file first, so the partner never
        # sees neither) — or nothing to hand over: the turn is the partner's.
        self._set_busy(False)

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
                    if self._auto_chat.get():
                        self._set_busy(True)   # before the message leaves the file
                    os.remove(AUTO_MSG_FILE)
                    # Display the peer's thinking block (display-only, not in conversation)
                    thinking = data.get("thinking", "").strip()
                    if thinking and self.show_thinking.get():
                        self.chat_display.config(state="normal")
                        self.chat_display.insert(tk.END, "Thinking:\n", "thinking_label")
                        self.chat_display.insert(tk.END, thinking + "\n\n", "thinking")
                        self.chat_display.see(tk.END)
                        self.chat_display.config(state="disabled")
                    # What the human has in the box: a message held for this
                    # answer (send_message) goes in with it; a draft is set
                    # aside and put back once this message is sent.
                    typed = self.input_field.get("1.0", "end-1c").strip()
                    if typed and self._held_for_partner:
                        text = f"{text}\n\n{typed}"
                    elif typed:
                        self._stashed_draft = typed
                    self._held_for_partner = False
                    self.input_field.delete("1.0", tk.END)
                    self.input_field.insert("1.0", text)
                    self.input_field.see("end")
                    self.root.update_idletasks()
                    delay = self._send_delay if self._send_delay > 0 else 0
                    if delay > 0:
                        self.input_field.config(state="disabled")
                        self.root.after(delay, self._auto_msg_delayed_send)
                    else:
                        self.send_message(from_peer=True)
                        self._restore_draft()
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
        self.send_message(from_peer=True)
        self._restore_draft()

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
        # The file stem, for the cost log's CHAT field (_log_api_cost runs after
        # this save in _finish_close).
        self._last_saved_chat_name = os.path.splitext(
            os.path.basename(self._chat_file_path(save_name)))[0]
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

    # ── Voice input — MyAgent's VoiceMixin, adjusted (2026-10-14) ──────────
    # The Mike / Auto-send row under the message box and the Voice Setup
    # button on the Debug row are built in setup_ui; the dialog, the dictation
    # state machine, the recorder and the transcription paths are the inherited
    # mixin's, over the per-user settings file MyAgent's Voice Setup edits
    # (one Voice Setup serves both apps on a machine). Four points are SelfBot's
    # own — see CLAUDE_SELFBOT.md's Voice input paragraph.

    @staticmethod
    def _voice_end_word(transcript):
        """SelfBot has no end words — a dictated "Stop." or "Quit." is a message
        to the bot like any other — so the row's end-word rule (MyAgent's, for
        the Agent Request dialog's Convo-mode words) lands nothing bare here:
        "" tells the row the transcript is no end word, and it lands as heard."""
        return ""

    def _voice_auto_send_changed(self):
        """The Auto-send box was toggled. Kept like SelfBot's other toggles: in
        app_state.json now (`_save_last_state`), and in the active system
        prompt's bundle at its next SAVE (`_capture_prompt_settings`), restored
        with the prompt (`_apply_prompt_settings`). MyAgent's writes the value
        into the applied instruction's store entry at once, because its editor
        has no widget for the setting; SelfBot's prompt editor SAVE snapshots
        every toggle, so this one follows that convention instead."""
        try:
            self._save_last_state()
        except Exception:
            pass

    def _voice_key_status(self, provider):
        """(message, missing) for the provider's API key line — VoiceMixin's,
        naming SelfBot as the app to restart."""
        attribute, name = {"Google": ("gemini_client", "GEMINI_API_KEY"),
                           "xAI": ("xai_client", "XAI_API_KEY")}.get(
            provider, ("openai_client", "OPENAI_API_KEY"))
        present = getattr(self, attribute, None) is not None
        if present:
            return f"{name} is set.", False
        return (f"{name} is NOT set: {provider} cannot transcribe until it is "
                "(set the environment variable, then restart SelfBot)."), True

    def _focus_for_reply(self, bell=False):
        """The focus once a reply has ended (check_queue's complete and error
        tails) and at a launch that restores the box ticked: with the Auto-send
        box ticked - the hands-free mode - the Mike button, so Enter or Space
        starts the next dictation at once (the user's request, 2026-10-14;
        MyAgent's Agent Request dialog opens on Mike the same way), unless
        something is already typed in the message box, where Enter must keep
        meaning send; otherwise the message box, as before. True when Mike
        took it. With `bell` - the reply tails, not the launch restore - Mike
        taking the focus also rings the display bell (Windows Default Beep,
        the Mac alert sound; nothing where system sounds are muted): the cue
        to press Space and speak, as MyAgent's dialog rings it when it opens
        on Mike (the user's request, later the same day). Tolerates a host
        without the row (no package, a bare test host)."""
        mike = getattr(self, "mike_button", None)
        box = getattr(self, "input_field", None)
        try:
            typed = box.get("1.0", "end-1c").strip() if box is not None else ""
            if mike is not None and self.dictation_auto_send.get() and not typed:
                mike.focus_set()
                root = getattr(self, "root", None)
                if bell and root is not None:
                    root.bell()
                return True
            if box is not None:
                box.focus_set()
        except tk.TclError:
            pass
        return False

    def _place_window(self, win, kind, default_size, parent=None, min_size=None):
        """MyAgent's dialog-placement entry point, for the inherited dialogs that
        call it (Voice Setup): SelfBot's `_place_dialog` — the geometry saved for
        `kind` when some monitor still shows it, else centred on the main window
        — applied and returned. `parent` is always the main window here;
        `min_size` is `_place_dialog`'s default when not given."""
        if min_size is None:
            return self._place_dialog(win, kind, default_size)
        return self._place_dialog(win, kind, default_size, min_size=min_size)

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
        # Log this process's cumulative API cost to the shared cost log — the
        # one argument list _session_log_args builds for the line and the
        # in-progress record alike (the zero-cost gate skips a session that
        # priced nothing) — then remove the record, which is for a session
        # that never gets here.
        try:
            self._log_api_cost(**self._session_log_args())
        except Exception:
            pass
        self._run_progress_clear()
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
        # Either instance may have written the opener file (_write_inject_file);
        # each removes its own busy mark (_set_busy)
        for shared_file in (INJECT_FILE, AUTO_MSG_FILE, self._busy_file()):
            try:
                os.remove(shared_file)
            except OSError:
                pass
        self._cleanup_browser()
        self.root.destroy()

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

        Handles the per-family quirks: Fable/Mythos thinking is always on (a
        stale "off" still takes the thinking branch and is sent as plain
        adaptive); adaptive thinking asks for display="summarized" so the Show
        Thinking pane isn't blank on Fable / Opus 4.7+ (their default became
        "omitted"); Sonnet 5+ "Off" is an explicit disable (omitting `thinking`
        runs adaptive there) — on Sonnet 5.5+ a bare `between_tools`, the only
        off switch that model takes (_is_sonnet_55); and Opus 4.7+ / Sonnet 5+ / Fable reject
        temperature (HTTP 400), so it's skipped for them. The Fable/Mythos
        class also carries Fable 5.1's `thinking.block_binding` (preserved
        thinking) and a `fallbacks` value (server-side refusal fallbacks) —
        see _fable_features; `fallbacks` sits here under its wire name so the
        debug dump shows it, and stream_worker moves it into extra_body
        because anthropic 0.84.0 has no typed kwarg for it."""
        # The model's own output ceiling, thinking on or off — the only bound
        # on adaptive reasoning (see _anthropic_output_cap).
        kwargs["max_tokens"] = self._anthropic_output_cap()
        always_on = self._is_always_on_thinking()
        fable = self._fable_features()
        if self.thinking_enabled or always_on:
            support = self._model_supports_thinking()
            if support == "adaptive":
                kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
                if fable and fable["block_binding"]:
                    kwargs["thinking"]["block_binding"] = fable["block_binding"]
                if self.thinking_mode not in ("off", "adaptive"):
                    kwargs["output_config"] = {"effort": self.thinking_mode}
            elif support == "manual":
                kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
        else:
            if self._thinking_on_by_default():
                # Sonnet 5.5+ refuses the explicit disable; its off switch is
                # between_tools, sent bare (display or the binding beside it
                # is a 400) and with no effort ("Off" never sends one, and
                # between_tools takes high or below only). See _is_sonnet_55.
                kwargs["thinking"] = ({"type": "between_tools"} if self._is_sonnet_55()
                                      else {"type": "disabled"})
            if not self._rejects_temperature() and self.model not in self._no_temperature:
                kwargs["temperature"] = self.temperature
        if fable and fable["fallbacks"]:
            kwargs["fallbacks"] = fable["fallbacks"]
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
        # read_document and the native file tools ride along unconditionally,
        # as in MyAgent — no checkbox (tool parity, 2026-10-14).
        tools.extend(copy.deepcopy(READ_DOCUMENT_TOOLS))
        tools.extend(copy.deepcopy(FILE_TOOLS))
        if self.desktop_enabled.get() and _HAS_DESKTOP:
            # find_element is Gemini's pointing API — MyAgent offers it to the
            # Google provider alone, so an Anthropic-only app never does.
            desktop = [t for t in copy.deepcopy(DESKTOP_TOOLS) if t["name"] != "find_element"]
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
        # MyAgent's Excel (xlwings) and Physical (camera / microphone) sets —
        # checkbox-gated like Browser; each Physical tool only with its package.
        if _HAS_MYAGENT_TOOLS and _HAS_EXCEL and self.excel_enabled.get():
            tools.extend(copy.deepcopy(EXCEL_TOOLS))
        if _HAS_MYAGENT_TOOLS and _HAS_PHYSICAL and self.physical_enabled.get():
            installed = {"camera_capture": _HAS_CAMERA, "microphone_listen": _HAS_MICROPHONE}
            tools.extend(copy.deepcopy(t) for t in PHYSICAL_TOOLS if installed.get(t["name"], True))
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
        if self.exit_enabled.get():
            tools.extend(copy.deepcopy(EXIT_TOOLS))
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
        if block.name == "exit_conversation":
            self._tool_info("exit_conversation\n")
            if not self.exit_enabled.get():     # unticked since the call began
                return "exit_conversation is not enabled — the Exit box is unticked."
            return self.do_exit_conversation((inp or {}).get("reason", ""))
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
        # Excel live-workbook tools (xlwings, ExcelMixin) — MyAgent's namespaced
        # dispatch; the _HAS_EXCEL check inside so a missing xlwings gives a
        # clear install hint instead of "Unknown tool".
        if block.name.startswith("excel_"):
            if not (_HAS_MYAGENT_TOOLS and _HAS_EXCEL):
                return ("Excel tools unavailable: xlwings is not installed. "
                        "Install with: pip install xlwings (desktop Excel must "
                        "also be installed).")
            if not self.excel_enabled.get():
                return f"Excel tools are disabled. Enable the Excel checkbox to use '{block.name}'."
            method = getattr(self, f"do_{block.name}", None)
            if method is None:
                return f"Unknown Excel tool: {block.name}"
            self._tool_info(f"Excel: {block.name}\n")
            return method(inp or {})
        # Physical tools (camera_capture via OpenCV, microphone_listen via
        # sounddevice — PhysicalMixin) — the same pattern.
        if block.name.startswith("camera_"):
            if not (_HAS_MYAGENT_TOOLS and _HAS_CAMERA):
                return ("Camera tools unavailable: OpenCV is not installed. "
                        "Install with: pip install opencv-python")
            if not self.physical_enabled.get():
                return f"Physical tools are disabled. Enable the Physical checkbox to use '{block.name}'."
            method = getattr(self, f"do_{block.name}", None)
            if method is None:
                return f"Unknown Physical tool: {block.name}"
            cam_inp = inp or {}
            cam_wait = f"waiting {cam_inp['delay_seconds']} s, then " if cam_inp.get("delay_seconds") else ""
            self._tool_info(f"Camera: {cam_wait}taking a photo (camera {cam_inp.get('camera') or 0})...\n")
            return method(cam_inp)
        if block.name.startswith("microphone_"):
            if not (_HAS_MYAGENT_TOOLS and _HAS_MICROPHONE):
                return ("Microphone tools unavailable: the 'sounddevice' package is not "
                        "installed. Install with: pip install sounddevice")
            if not self.physical_enabled.get():
                return f"Physical tools are disabled. Enable the Physical checkbox to use '{block.name}'."
            method = getattr(self, f"do_{block.name}", None)
            if method is None:
                return f"Unknown Physical tool: {block.name}"
            mic_inp = inp or {}
            asked = mic_inp.get("seconds")
            shown = asked if asked not in (None, "") else getattr(self, "MIC_DEFAULT_SECONDS", 5)
            shown = f"{shown:g}" if isinstance(shown, (int, float)) else shown
            self._tool_info(f"Microphone: listening for {shown} s...\n")
            return method(mic_inp)
        if block.name == "read_document":
            fp = (inp or {}).get("path", "")
            self._tool_info(f"Reading document: {os.path.basename(fp)}\n")
            return self.do_read_document(inp or {})
        if block.name in ("read_file", "write_file", "edit_file",
                          "glob_files", "grep_files"):
            # Native file tools (FileMixin) — dynamic dispatch like the mail
            # mixins; the label shows the path or pattern being operated on.
            label = (inp or {}).get("path") or (inp or {}).get("pattern") or ""
            self._tool_info(f"{block.name}: {label}\n")
            return getattr(self, f"do_{block.name}")(inp or {})
        if block.name in ("screenshot", "mouse_click", "type_text",
                             "press_key", "mouse_scroll", "open_application",
                             "find_window", "clipboard_read", "clipboard_write",
                             "wait_for_window", "read_screen_text",
                             "find_image_on_screen", "mouse_drag", "find_element",
                             "view_image"):
            if not self.desktop_enabled.get():
                return "Desktop control is disabled. Enable the Desktop checkbox to use this tool."
            click_display = inp.get("display")
            if click_display is not None:
                click_display = int(click_display)
            if block.name == "view_image":
                path = str(inp.get("path", "") or "")
                self._tool_info(f"Viewing image: {os.path.basename(path)}\n")
                return self.do_view_image(path)
            if block.name == "screenshot":
                display = click_display  # alias for clarity in screenshot path
                disp_label = f"display {display}" if display is not None else "all displays"
                self._tool_info(f"Taking screenshot ({disp_label})...\n")
                region = None
                if all(k in inp for k in ("x", "y", "width", "height")):
                    region = (inp["x"], inp["y"], inp["width"], inp["height"])
                return self.do_screenshot(region, display=display, grid=bool(inp.get("grid", False)))
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
                    display=click_display,
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
                sx, sy = inp.get("x"), inp.get("y")
                self._tool_info(f"Scrolling {clicks_val} clicks...\n")
                return self.do_mouse_scroll(clicks_val, x=sx, y=sy, display=click_display)
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
                return self.do_read_screen_text(rx, ry, rw, rh, display=click_display)
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
                    display=click_display,
                )
            if block.name == "find_element":
                # Gemini's pointing API: never offered here (_get_tools strips it),
                # answered for a stray call the way MyAgent answers a non-Google provider.
                return ("find_element is not available in SelfBot (it needs the Gemini "
                        "pointing API). Use a region screenshot or grid=true overlay instead.")
        elif block.name in ("browser_open", "browser_navigate",
                              "browser_click", "browser_download",
                              "browser_fill",
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
            if block.name == "browser_download":
                sel = inp.get("selector", "")
                txt = inp.get("text", "")
                target = sel or f"text='{txt}'"
                path = inp.get("save_path", "")
                self._tool_info(f"Browser: downloading via {target} -> {path}\n")
                return self.do_browser_download(
                    save_path=path, selector=sel or None, text=txt or None,
                    timeout_s=inp.get("timeout_s", 60))
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

        # --- Bundled resource files (2026-09-25, mirrors MyAgent's
        # skills_mixin): the folder IS the skill — SKILL.md may sit beside
        # references/, scripts/, tests/, … which sync, copy and delete with
        # it. Addressed by skill name + a RELATIVE file_path, validated by
        # _skill_resource_path (never absolute, never '..', never SKILL.md).
        if action == "list_files":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            rels = _list_skill_resources(SKILLS_DIR, name)
            if rels is None:
                return (f"Skill '{name}' has no folder on disk yet — its bundled "
                        "files appear after the first save.")
            if not rels:
                return f"Skill '{name}' has no bundled resource files (SKILL.md only)."
            folder = _skill_dir_for(SKILLS_DIR, name)
            lines = []
            for rel in rels:
                try:
                    size = os.path.getsize(os.path.join(folder, rel))
                except OSError:
                    size = 0
                lines.append(f"• {rel.replace(os.sep, '/')}  ({size} bytes)")
            return f"Bundled resource files of '{name}':\n" + "\n".join(lines)

        if action == "read_file":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            rel_arg = params.get("file_path", "")
            path, err = _skill_resource_path(SKILLS_DIR, name, rel_arg)
            if err:
                return f"Error: {err}."
            if not os.path.isfile(path):
                return (f"Error: '{rel_arg}' does not exist in skill '{name}' "
                        "(use list_files).")
            try:
                with open(path, "rb") as f:
                    raw = f.read(200_001)
            except OSError as e:
                return f"Error reading '{rel_arg}': {e}"
            if b"\x00" in raw[:8192]:
                return (f"'{rel_arg}' is a binary file "
                        f"({os.path.getsize(path)} bytes) — read_file returns text only.")
            text = raw.decode("utf-8", errors="replace")
            if len(raw) > 200_000:
                text = text[:200_000] + "\n...[truncated at 200,000 bytes]"
            return text

        if action == "write_file":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            file_content = params.get("file_content")
            if file_content is None:
                return "Error: 'file_content' is required for write_file."
            rel_arg = params.get("file_path", "")
            path, err = _skill_resource_path(SKILLS_DIR, name, rel_arg)
            if err:
                return f"Error: {err}."
            existed = os.path.isfile(path)
            try:
                import tempfile
                os.makedirs(os.path.dirname(path), exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                    f.write(file_content)
                os.replace(tmp, path)
            except OSError as e:
                return f"Error writing '{rel_arg}': {e}"
            verb = "Replaced" if existed else "Wrote"
            return (f"{verb} '{rel_arg}' in skill '{name}' "
                    f"({len(file_content.encode('utf-8'))} bytes).")

        if action == "delete_file":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            rel_arg = params.get("file_path", "")
            path, err = _skill_resource_path(SKILLS_DIR, name, rel_arg)
            if err:
                return f"Error: {err}."
            if not os.path.isfile(path):
                return f"Error: '{rel_arg}' does not exist in skill '{name}'."
            try:
                os.remove(path)
            except OSError as e:
                return f"Error deleting '{rel_arg}': {e}"
            # Prune now-empty subdirectories, never the skill folder itself
            folder = _skill_dir_for(SKILLS_DIR, name)
            parent = os.path.dirname(path)
            while folder and os.path.realpath(parent) != os.path.realpath(folder):
                try:
                    os.rmdir(parent)   # only succeeds when empty
                except OSError:
                    break
                parent = os.path.dirname(parent)
            return f"Deleted '{rel_arg}' from skill '{name}'."

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
            self._set_skill_mode(name, mode)
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
                self._set_skill_mode(name, mode)
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
            (getattr(self, "_skill_global_modes", None) or {}).pop(name, None)
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
                           "excel", "physical", "exit", "dictation_auto_send")
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
            if self._save_prompts_to_disk(prompts) is False:
                return self._PROMPTS_UNREADABLE_RESULT
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
            if self._save_prompts_to_disk(prompts) is False:
                return self._PROMPTS_UNREADABLE_RESULT
            return f"Updated system prompt '{name}'."
        if action == "delete":
            if name not in prompts:
                return f"Prompt not found: {name}"
            if name == "Default":
                return "Cannot delete the 'Default' prompt."
            del prompts[name]
            if self._save_prompts_to_disk(prompts) is False:
                return self._PROMPTS_UNREADABLE_RESULT
            return f"Deleted system prompt '{name}'."
        return f"Unknown action: {action}"

    _PROMPTS_UNREADABLE_RESULT = (
        "Error: the system prompt store could not be read, so nothing was saved "
        "(saving would have replaced the whole library). The user must fix or "
        "restore system_prompts.json and restart SelfBot.")

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

    # ── Claude Fable 5.1 harness surface (2026-09-02) ──────────────────────────
    # In-file copies of AnthropicMixin._anthropic_fable_features / _refusal_note
    # and helpers.strip_thinking_blocks / strip_pre_fallback_blocks — SelfBot
    # keeps its own, like _trim_history_for_context and _cache_messages, because
    # the myagent import is the optional/stubbed kind. Rationale for each lives
    # in myagent/constants.py (the beta constants) and CLAUDE_MYAGENT.md.

    def _anthropic_server_tools(self):
        """The Anthropic server-side tool declarations for the current model —
        (tools, extra_betas). Since 2026-09-25 the default is the CURRENT
        triple: web_search_20260209 + web_fetch_20260209 (dynamic filtering,
        no beta) and code_execution_20260521 — which also makes code
        execution FREE (no container-hour billing while a 20260209 web tool
        is declared, per the pricing page) and adds a real web-fetch
        capability Anthropic runs never had. Probed live 2026-09-25: every
        served model accepts the triple — Fable/Mythos 5 and 5.1, Opus 5.5 /
        5 / 4.5, Sonnet 5 / 4.5 — EXCEPT Haiku 4.5, whose 400 says it "does
        not support programmatic tool calling" (what the 20260209 filtering
        runs on); Haiku 4.5 keeps the older web pair (web_fetch_20250910
        under its beta) with the new code-execution type, also probed.
        Haiku 5.5 (2026-10-08) takes the triple like every other current
        model, so the fallback is gated to Haiku BELOW 5. A future
        model rejecting the triple is learned off per session by the
        BadRequest rung in _stream_anthropic_call ("server_tools_20260209"
        in _anthropic_unsupported) and retried on the fallback set. The
        definitions cost ~6.4K input tokens against the old pair's ~4K,
        absorbed by the prompt cache after the first call."""
        # Haiku BELOW 5 only: Haiku 5.5 accepts the triple (probed live
        # 2026-10-08 — HTTP 200 with the ~6.5K tokens of definitions;
        # its capability tree lists web_search + code_execution), so the
        # rule is version-parsed rather than the family prefix it was.
        haiku = self._parse_claude_major_minor(self.model or "", ("claude-haiku-",))
        fallback = (haiku is not None and haiku < (5, 0)) or (
            "server_tools_20260209" in getattr(self, "_anthropic_unsupported", set()))
        if fallback:
            return ([{"type": "web_search_20250305", "name": "web_search"},
                     {"type": "web_fetch_20250910", "name": "web_fetch"},
                     {"type": "code_execution_20260521", "name": "code_execution"}],
                    ["web-fetch-2025-09-10"])
        return ([{"type": "web_search_20260209", "name": "web_search"},
                 {"type": "web_fetch_20260209", "name": "web_fetch"},
                 {"type": "code_execution_20260521", "name": "code_execution"}],
                [])

    def _fable_features(self):
        """The Fable/Mythos-only request surface for this call, or None for
        every other model: the beta headers to add plus the
        `thinking.block_binding` (Fable 5.1's preserved-thinking check —
        `drop_block` makes a history edit such as the overflow trim, a system
        prompt switch or a tool-toggle change degrade instead of 400) and
        `fallbacks` (server-side refusal fallbacks) values to send, minus any
        surface this process has already seen the API reject
        (`_anthropic_unsupported`, filled by the stream_worker 400 rungs)."""
        always_on = self._is_always_on_thinking()
        sonnet55 = self._is_sonnet_55()
        # Haiku 5.5+ (2026-10-08) runs the preserved-thinking check too — the
        # binding is accepted beside adaptive thinking and refused beside a
        # disable, which is why _apply_thinking_params adds it in the adaptive
        # branch only (and stream_worker keeps its beta header off a disabled
        # call) — but it has NO server-side fallback: a list is a 400 and
        # "default" is accepted and does nothing. Probed live for MyAgent.
        # Sonnet 5.5+ (2026-10-14, probed live for SelfBot) takes both: the
        # binding beside adaptive thinking (refused beside between_tools —
        # the same adaptive-branch rule) and `fallbacks: "default"` beside
        # either shape; a model list is refused there.
        if not (always_on or sonnet55 or self._is_haiku_5()):
            return None
        unsupported = getattr(self, "_anthropic_unsupported", set())
        features = {"betas": [], "block_binding": None, "fallbacks": None}
        if "block_binding" not in unsupported:
            features["betas"].append(ANTHROPIC_THINKING_BINDING_BETA)
            features["block_binding"] = dict(ANTHROPIC_THINKING_BLOCK_BINDING)
        if (always_on or sonnet55) and "fallbacks" not in unsupported:
            features["betas"].append(ANTHROPIC_SERVER_FALLBACK_BETA)
            features["fallbacks"] = ANTHROPIC_SERVER_FALLBACKS
        return features

    @staticmethod
    def _anthropic_output_cap_for(model_id):
        """The family table's output ceiling for an Anthropic id — the longest
        matching prefix of ANTHROPIC_MAX_OUTPUT_TOKENS, so a dated snapshot
        rides with its family — and ANTHROPIC_MAX_OUTPUT_DEFAULT for an id no
        row names (the 400 rung in _stream_anthropic_call corrects a guess
        the API refuses)."""
        best = None
        for prefix, cap in ANTHROPIC_MAX_OUTPUT_TOKENS:
            if (model_id or "").startswith(prefix) and (best is None or len(prefix) > len(best[0])):
                best = (prefix, cap)
        return best[1] if best else ANTHROPIC_MAX_OUTPUT_DEFAULT

    def _anthropic_output_cap(self, model=None):
        """The output ceiling an Anthropic call sends as `max_tokens` — the
        model's OWN maximum, thinking on or off: the startup listing's
        `max_tokens` for the id (`_anthropic_output_caps`, filled by
        _fetch_available_models and by a 400 naming a lower ceiling), else
        the family table. Nothing else bounds adaptive thinking, so a smaller
        figure cut a long reasoning phase off, billed and lost — see
        ANTHROPIC_MAX_OUTPUT_TOKENS in constants.py. The Debug dump reads the
        same helper, so it cannot drift from the wire."""
        model = model or self.model
        caps = getattr(self, "_anthropic_output_caps", None) or {}
        cap = caps.get(model)
        if isinstance(cap, int) and not isinstance(cap, bool) and cap > 0:
            return cap
        return self._anthropic_output_cap_for(model)

    @staticmethod
    def _thinking_drop_why(reasons):
        """Why the API dropped replayed thinking blocks, from the reasons its
        `input_transformations` gave, in plain words for the Activity line
        (MyAgent's helper, worded for SelfBot): model_binding_mismatch — they
        were written by another model (a mid-chat model switch, or a refusal
        fallback's), and only the model that wrote a block reads it;
        prefix_binding_mismatch — earlier history changed since they were
        written (the overflow trim, a system-prompt switch, a tool-toggle
        change); anything else as given."""
        words = {
            "model_binding_mismatch": ("they were written by another model (a mid-chat model "
                                       "switch, or a refusal fallback's), which this model "
                                       "cannot read"),
            "prefix_binding_mismatch": "earlier history changed since they were written",
        }
        return "; ".join(words.get(r, f"reason: {r}") for r in reasons)

    @staticmethod
    def _refusal_note(stop_details, fallbacks_requested=False):
        """The ⚠ line for a stop_reason="refusal" response. `stop_details` is
        whatever rode on the final message_delta — a plain dict on anthropic
        0.84.0 (untyped there, so the SDK snapshot never copies it and
        stream_worker captures it off the event), an object on newer SDKs, or
        None (branch on stop_reason, never on this)."""
        def field(name):
            if isinstance(stop_details, dict):
                return stop_details.get(name)
            return getattr(stop_details, name, None) if stop_details is not None else None
        category = field("category")
        explanation = field("explanation")
        recommended = field("recommended_model")
        note = "⚠ The model declined this request (stop_reason=refusal"
        if category:
            note += f", category={category}"
        note += ")"
        if explanation:
            note += f": {explanation}"
        note += "\n  Any partial output above is incomplete."
        if fallbacks_requested:
            note += " A server-side fallback was requested but no fallback model served it"
            if recommended:
                note += f" — the API suggests retrying directly on {recommended}"
            note += "."
        note += " Rephrase, or switch the model to claude-opus-5.\n"
        return note

    @staticmethod
    def _block_type(block):
        return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)

    @staticmethod
    def _block_field(block, name):
        return block.get(name) if isinstance(block, dict) else getattr(block, name, None)

    @classmethod
    def _strip_thinking_blocks(cls, messages):
        """Remove every thinking / redacted_thinking block from the assistant
        turns of `messages` IN PLACE (text and tool_use blocks stay); returns
        the count removed. The no-beta recovery for Fable 5.1's preserved-
        thinking check: a replayed block whose conversation prefix changed is
        a 400 ("Invalid `signature` in `thinking` block … bound to a different
        conversation") on enforced accounts — strip once, retry once. A turn
        that would end up empty is left alone (an empty content list is itself
        a 400). Handles dict blocks (loaded chats) and SDK objects alike."""
        removed = 0
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            kept = [b for b in content
                    if cls._block_type(b) not in ("thinking", "redacted_thinking")]
            if kept and len(kept) != len(content):
                removed += len(content) - len(kept)
                msg["content"] = kept
        return removed

    @classmethod
    def _strip_pre_fallback_blocks(cls, blocks):
        """Content blocks to echo back after a server-side refusal fallback
        switched models mid-output (a `fallback` block marks each switch).
        Before the LAST marker only text blocks and PAIRED server-tool blocks
        survive — the declined model's thinking and tool_use blocks are
        neither echoed nor (since the tool loop scans the returned list)
        executed; everything after the boundary is the serving model's own
        turn. The markers are dropped too (ignorable audit markers, and
        anthropic 0.84.0 can't re-serialize the unknown block type). A
        response with no marker is returned unchanged (same list object)."""
        last = -1
        for i, block in enumerate(blocks):
            if cls._block_type(block) == "fallback":
                last = i
        if last < 0:
            return blocks
        pre = blocks[:last]
        result_ids = {cls._block_field(b, "tool_use_id") for b in pre
                      if (cls._block_type(b) or "").endswith("_tool_result")}
        kept, kept_use_ids = [], set()
        for block in pre:
            btype = cls._block_type(block) or ""
            if btype == "text":
                kept.append(block)
            elif btype == "server_tool_use":
                bid = cls._block_field(block, "id")
                if bid in result_ids:
                    kept.append(block)
                    kept_use_ids.add(bid)
            elif btype.endswith("_tool_result"):
                if cls._block_field(block, "tool_use_id") in kept_use_ids:
                    kept.append(block)
        kept.extend(b for b in blocks[last + 1:] if cls._block_type(b) != "fallback")
        return kept

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
        # The reply's clock, for the cost log's TIME field (_session_secs):
        # this reply's wall-clock minus the seconds it sits on a
        # confirmation dialog, which helpers.input_wait_timer accumulates
        # into _input_wait_secs — MyAgent's run clock, per reply.
        self._reply_started = time.monotonic()
        self._input_wait_secs = 0.0
        try:
            # Sync temperature from spinbox in case user typed a value without pressing Enter
            try:
                self.temperature = max(0.0, min(1.0, self._temp_var.get()))
            except (tk.TclError, ValueError):
                pass

            # MCP servers connect in the background (the inherited MCPMixin):
            # a message sent while they are still connecting waits here, on
            # the worker, so its tool list includes them. The stub mixin used
            # without the myagent package has no such method.
            wait_mcp = getattr(self, "_mcp_wait_ready", None)
            if wait_mcp is not None:
                wait_mcp()

            # Every call of this reply sends the system prompt the first one
            # does (_build_system_prompt says why) — a skill or prompt changed
            # by a tool mid-reply reaches the next message. Let go in finally.
            self._freeze_system_prompt()

            # Only emit label upfront if thinking is disabled
            label_emitted = False
            if not self.thinking_enabled:
                self.queue.put({"type": "label"})
                label_emitted = True

            call_num = 0
            while True:
                call_num += 1
                self._session_calls += 1
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
                # Add Anthropic server-side tools (current triple, or Haiku's
                # fallback set — see _anthropic_server_tools)
                server_tools, server_betas = self._anthropic_server_tools()
                tools.extend(server_tools)
                api_kwargs = {
                    "model": self.model,
                    "system": self._cache_system(self._build_system_prompt()),
                    "messages": messages,  # replaced per attempt with the breakpointed copy
                    "tools": tools,
                }
                self._apply_thinking_params(api_kwargs)
                # Fable 5.1 surface: `fallbacks` has no typed kwarg in anthropic
                # 0.84.0 (a typed call would raise) — extra_body merges it into
                # the JSON body as-is; the two extra betas ride the header.
                fable = self._fable_features()
                if "fallbacks" in api_kwargs:
                    api_kwargs["extra_body"] = {"fallbacks": api_kwargs.pop("fallbacks")}
                betas = ANTHROPIC_BETAS + server_betas + (fable["betas"] if fable else [])
                if api_kwargs.get("thinking", {}).get("type") in ("disabled", "between_tools"):
                    # Haiku 5.5's explicit disable and Sonnet 5.5's between_tools
                    # refuse the binding field (the builder keeps it off that
                    # branch); its beta header goes too.
                    betas = [b for b in betas if b != ANTHROPIC_THINKING_BINDING_BETA]
                stripped_thinking = False  # one-shot guard for the signature-400 rung
                stop_details = None

                for attempt in range(max_retries):
                    # Rebuilt every attempt, not once above the loop: the retry
                    # paths below mutate `messages` in place (the overflow trim's
                    # `del messages[:cut_at]`), so the wire copy has to be
                    # re-derived or the retry would resend the pre-trim history —
                    # and the rolling breakpoints belong on the post-trim tail.
                    api_kwargs["messages"] = self._cache_messages(messages)
                    try:
                        with self.client.beta.messages.stream(betas=betas, **api_kwargs) as stream:
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
                                        elif tool_name == "web_fetch":
                                            self._tool_info("Fetching a web page...\n")
                                        elif tool_name == "code_execution":
                                            self._tool_info("Running code execution...\n")
                                    elif hasattr(block, "type") and block.type in (
                                            "code_execution_tool_result", "bash_code_execution_tool_result",
                                            "web_search_tool_result", "web_fetch_tool_result"):
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
                                elif event.type == "message_delta":
                                    # stop_details (refusal category/explanation) rides
                                    # on the delta; anthropic 0.84.0's snapshot copies
                                    # only stop_reason/stop_sequence, so read it here.
                                    stop_details = getattr(event.delta, "stop_details", None) or stop_details

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
                        # Fable 5.1's preserved-thinking check: a replayed thinking
                        # block whose conversation prefix changed (the trim above, a
                        # system-prompt switch, a tool-toggle change, a reloaded chat
                        # under a different prompt) is a 400 on enforced accounts.
                        # The drop_block binding normally has the API drop such
                        # blocks instead; this rung is the no-beta floor — strip and
                        # retry once. Checked BEFORE the beta-surface rungs because
                        # this error text also names block_binding as the remedy.
                        if (e.status_code == 400 and "signature" in emsg and "thinking" in emsg
                                and not stripped_thinking):
                            stripped_thinking = True
                            removed = self._strip_thinking_blocks(messages)
                            if removed:
                                self.queue.put({"type": "warning", "content":
                                    f"⚠ The API rejected {removed} replayed thinking block(s) — "
                                    "earlier history changed since they were produced. Retrying "
                                    "without them; the model continues without that reasoning.\n"})
                                full_text = ""
                                continue
                        # A Fable beta surface this API key isn't enrolled in (the
                        # 400 names it): learn once for the process, drop it, retry.
                        if e.status_code == 400 and fable and fable["fallbacks"] and "fallback" in emsg:
                            self._anthropic_unsupported.add("fallbacks")
                            fable["fallbacks"] = None
                            api_kwargs.pop("extra_body", None)
                            betas = [b for b in betas if b != ANTHROPIC_SERVER_FALLBACK_BETA]
                            self._tool_info("Server-side refusal fallbacks are not available on "
                                            "this API key — retrying without them...\n")
                            full_text = ""
                            continue
                        if (e.status_code == 400 and fable and fable["block_binding"]
                                and ("block_binding" in emsg or "thinking-binding" in emsg)):
                            self._anthropic_unsupported.add("block_binding")
                            fable["block_binding"] = None
                            api_kwargs.get("thinking", {}).pop("block_binding", None)
                            betas = [b for b in betas if b != ANTHROPIC_THINKING_BINDING_BETA]
                            self._tool_info("Thinking-binding controls are not available on "
                                            "this API key — retrying without them...\n")
                            full_text = ""
                            continue
                        # A model that rejects the current 20260209 server-tool
                        # triple (Haiku 4.5's shape — gated up front — or a
                        # future model the gate over-admits): learn once for
                        # the session, rebuild on the fallback set, retry.
                        if (e.status_code == 400
                                and "server_tools_20260209" not in self._anthropic_unsupported
                                and ("programmatic tool calling" in emsg
                                     or "web_search_20260209" in emsg
                                     or "web_fetch_20260209" in emsg)):
                            self._anthropic_unsupported.add("server_tools_20260209")
                            server_tools, server_betas = self._anthropic_server_tools()
                            api_kwargs["tools"] = self._get_tools() + server_tools
                            betas = betas + server_betas
                            self._tool_info(f"{self.model} does not take the current server-side "
                                            "web tools — retrying with the older variants...\n")
                            full_text = ""
                            continue
                        # max_tokens above the model's ceiling ("max_tokens: 200000 >
                        # 64000, which is the maximum allowed number of output tokens
                        # for <model>" — the wording MyAgent probed live 2026-10-11):
                        # an id the startup listing did not describe and the family
                        # table over-estimates. Learn the ceiling for the process
                        # (the listing's own key), retry at it. A validation 400 —
                        # nothing was billed.
                        real_cap = parse_output_cap(emsg)
                        if e.status_code == 400 and real_cap and real_cap < api_kwargs.get("max_tokens", 0):
                            caps = getattr(self, "_anthropic_output_caps", None)
                            if caps is None:
                                caps = self._anthropic_output_caps = {}
                            caps[self.model] = real_cap
                            api_kwargs["max_tokens"] = real_cap
                            self._tool_info(f"{self.model} takes at most {real_cap:,} output tokens "
                                            "per call — retrying at that ceiling...\n")
                            full_text = ""
                            continue
                        # HTTP 413 request_too_large: the BODY is over the API's 32 MB
                        # limit — bytes, not tokens, so the overflow rung above never
                        # sees it, and the bytes are the screenshots: every one a
                        # desktop-driving chat took is still in the history, ~1 MB of
                        # base64 each at the resize cap (MyAgent's rung of 2026-10-13).
                        # Replace the oldest half with a note (the text and tool
                        # history stays whole), retry on the same list — the rebuild
                        # at the top of the loop picks it up. No image left to drop:
                        # fall back to the overflow rung's round trim, then give up.
                        if e.status_code == 413:
                            removed = strip_old_images(messages)
                            if removed:
                                self.queue.put({"type": "warning", "content":
                                    f"⚠ The request grew past the API's size limit (HTTP 413) — "
                                    f"removed the {removed} oldest screenshot/image(s) from the "
                                    "history and retried; the newest ones and all the text stay. "
                                    "The prompt cache is rebuilt once.\n"})
                                full_text = ""
                                continue
                            removed = self._trim_history_for_context(messages)
                            if removed > 0:
                                self.queue.put({"type": "warning", "content":
                                    f"⚠ The request grew past the API's size limit (HTTP 413) with "
                                    f"no images to drop — dropped the {removed} oldest message(s) "
                                    "and retried; earlier history is no longer in context.\n"})
                                full_text = ""
                                continue
                            raise RuntimeError(
                                f"Request too large on {self.model} (HTTP 413) — nothing left to "
                                "remove") from None
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
                    except anthropic.APIConnectionError as e:
                        # No HTTP status at all: the request never reached the API,
                        # or the stream broke under it (APITimeoutError is a
                        # subclass). The SDK has already retried it twice, quickly,
                        # before raising; here it used to end the reply outright —
                        # in a duo chat, the conversation (MyAgent's rung of
                        # 2026-10-13). A rung of its own, after APIStatusError (a
                        # sibling under APIError, so neither catches the other):
                        # wait, retry the same request. A mid-stream break restarts
                        # the call — the partial text already shown is streamed
                        # again, as after a 429.
                        if attempt < max_retries - 1:
                            wait = connection_error_backoff(attempt)
                            detail = (getattr(e, "message", "") or str(e) or "Connection error").strip()
                            self.queue.put({
                                "type": "tool_info",
                                "content": f"{detail} — retrying in {wait}s "
                                           f"(attempt {attempt + 1}/{max_retries})...\n",
                            })
                            time.sleep(wait)
                            full_text = ""
                        else:
                            raise

                # Preserved-thinking bookkeeping (Fable 5.1+, thinking-binding beta):
                # with the header on, every response lists the replayed thinking
                # blocks the API dropped before the model saw them (a history edit,
                # or blocks from another model after a mid-chat model switch).
                # Unbilled, but the model re-plans without that reasoning.
                # Said ONCE per conversation per shape (count + reasons), not on
                # every call: after a mid-chat model switch the earlier model's
                # blocks drop on every later call — the same N, the same reason —
                # and a line per call read like a fault (MyAgent's 2026-10-11
                # report). A new count or reason is a new line; a new chat resets.
                transformations = getattr(final_message, "input_transformations", None) or []
                if transformations:
                    reasons = sorted({str((t.get("reason") if isinstance(t, dict)
                                           else getattr(t, "reason", None)) or "unknown")
                                      for t in transformations})
                    shape = (len(transformations), tuple(reasons))
                    if shape != getattr(self, "_thinking_drops_noted", None):
                        self._thinking_drops_noted = shape
                        self._tool_info(f"API dropped {len(transformations)} replayed thinking "
                                        f"block(s) — {self._thinking_drop_why(reasons)}; the model "
                                        "continues without that reasoning (not billed, and the "
                                        "text and tool history is intact).\n")

                # Server-side refusal fallback: `message.model` names the model that
                # produced this message; a `fallback_message` entry in
                # usage.iterations is the served-by signal (covers the sticky
                # follow-up turns too, which carry no `fallback` content block).
                served_model = getattr(final_message, "model", None) or self.model
                usage = getattr(final_message, "usage", None)
                iterations = getattr(usage, "iterations", None) or []
                fallback_ran = any(
                    (it.get("type") if isinstance(it, dict) else getattr(it, "type", None)) == "fallback_message"
                    for it in iterations)
                content_blocks = self._strip_pre_fallback_blocks(list(final_message.content))
                if fallback_ran and final_message.stop_reason != "refusal":
                    self.queue.put({"type": "warning", "content":
                        f"⚠ {self.model} declined this request (safety classifier) — served by "
                        f"{served_model} via server-side fallback; this call bills at "
                        f"{served_model} rates.\n"})
                # The Fable classifiers can decline a request with HTTP 200 and
                # stop_reason="refusal" (pre-output: empty content, unbilled) — without
                # this notice the reply would just be silently blank. With fallbacks
                # on, a refusal here means the whole chain declined.
                if final_message.stop_reason == "refusal":
                    self.queue.put({"type": "warning", "content": self._refusal_note(
                        stop_details, fallbacks_requested=bool(fable and fable["fallbacks"]))})

                # --- API cost tracking (Anthropic) — accumulate this API call ---
                if usage:
                    # The zero-cost gate's flag (MyAgent's had_usage): a call
                    # answered with usage, priced or not.
                    self._session_had_usage = True
                    ci = getattr(usage, "input_tokens", 0) or 0
                    co = getattr(usage, "output_tokens", 0) or 0
                    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
                    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
                    # The session's token buckets run for every call that
                    # reported usage, priced or not (MyAgent's rule since
                    # 2026-09-15): the cost line records them either way...
                    self._session_tokens_in += ci
                    self._session_tokens_out += co
                    self._session_tokens_cache_write += cw
                    self._session_tokens_cache_read += cr
                    # ...and under the model that served this call, for the
                    # cost log's per-model split (a mid-chat model switch, or
                    # a refusal fallback served by an Opus tier): a dated
                    # snapshot the API echoes for the configured alias is the
                    # same model, kept under the name the MODEL field shows.
                    # The call's cost joins part[0] once it is priced.
                    served_key = served_model
                    if re.fullmatch(re.escape(self.model) + r"-\d{8}", served_key):
                        served_key = self.model
                    part = self._session_per_model.setdefault(served_key, [0.0, 0, 0, 0, 0])
                    part[1] += ci
                    part[2] += co
                    part[3] += cw
                    part[4] += cr
                    # Priced by the model that actually served the call (an Opus-tier
                    # fallback bills at ITS rates), falling back to the configured row —
                    # on the rate card the whole prompt (the uncached input plus both
                    # cache buckets) selects where a model has two: Haiku 5.5 above
                    # 100K tokens (ANTHROPIC_LONG_CONTEXT_PRICING, MyAgent 2026-10-08).
                    call_prompt = ci + cw + cr
                    pricing = (self._get_pricing(served_model, call_prompt)
                               or self._get_pricing(self.model, call_prompt))
                    if pricing:
                        # input_tokens is the NON-cached count; cache-write/read are
                        # disjoint buckets, each with its own rate — no double-counting.
                        call_cost = (ci * pricing["input"] + co * pricing["output"]
                                     + cw * pricing["cache_write"] + cr * pricing["cache_read"])
                        # Server-side web_search bills a flat $10/1,000 per
                        # EXECUTED search on top of tokens (2026-09-25; the
                        # count rides usage.server_tool_use, unread before).
                        call_cost += (getattr(getattr(usage, "server_tool_use", None),
                                              "web_search_requests", 0) or 0) * ANTHROPIC_WEB_SEARCH_FEE
                        self._session_cost += call_cost
                        part[0] += call_cost
                        if pricing.get("long_context") and not getattr(self, "_long_context_noted", False):
                            self._long_context_noted = True
                            self.queue.put({"type": "warning", "content": (
                                f"⚠ Prompt of {call_prompt:,} tokens (input plus cache) — over the "
                                f"{pricing['long_context']:,}-token line where {served_model} moves "
                                f"to its second rate card: this call, and every later one while "
                                f"prompts stay this long, are priced at ${pricing['input'] * 1e6:.2f} "
                                f"/ ${pricing['output'] * 1e6:.2f} per MTok instead of the base "
                                "rates.\n")})
                        self.queue.put({
                            "type": "cost_update",
                            "call_cost": call_cost,
                            "total_cost": self._session_cost,
                            "input_tokens": ci,
                            "output_tokens": co,
                            "cache_write_tokens": cw,
                            "cache_read_tokens": cr,
                        })
                    else:
                        # No pricing row for the model (MyAgent's unpriced
                        # warnings, 2026-09-23): said once per model, the call's
                        # tokens still shown, and the close will skip the line —
                        # a $0.0000 row would claim the session was free when
                        # the truth is that the price is unknown.
                        if served_model not in self._unpriced_noted:
                            self._unpriced_noted.add(served_model)
                            self.queue.put({"type": "warning", "content": (
                                f"⚠ {served_model} has no row in the Anthropic pricing table, so "
                                "its calls cannot be priced and this session will NOT be written "
                                "to the API cost log (a $0.0000 line would claim it was free). "
                                "Add the row in myagent/constants.py.\n")})
                        self.queue.put({
                            "type": "cost_update",
                            "call_cost": None,
                            "total_cost": None,
                            "input_tokens": ci,
                            "output_tokens": co,
                            "cache_write_tokens": cw,
                            "cache_read_tokens": cr,
                        })
                    # The in-progress record (selfbot_run_<N>.json): the line
                    # _finish_close would write if the session ended now,
                    # rewritten after every call, so a SelfBot killed from
                    # outside — the duo's taskkill, a crash, a reboot — still
                    # gets its line at the next launch (_run_progress_fold_in).
                    self._run_progress_write(**self._session_log_args())

                if final_message.stop_reason == "tool_use":
                    # Append the full assistant message (with tool_use blocks) to history
                    messages.append({"role": "assistant", "content": content_blocks})

                    tool_blocks = [b for b in content_blocks if b.type == "tool_use"]

                    # -- Parallel-safe tools (network I/O, pure lookups) --
                    # MyAgent's parallel-safe set (read-only, no shared state) plus SelfBot's own
                    PARALLEL_SAFE = {"csv_search", "get_skill"} | set(PARALLEL_SAFE_TOOLS)

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
            # A pre-output refusal leaves full_text empty; an empty assistant
            # message would 400 the NEXT request ("all messages must have
            # non-empty content"), so it is simply not appended — consecutive
            # user turns are legal and the API merges them.
            if full_text:
                self.messages.append({"role": "assistant", "content": full_text})
            self.queue.put({"type": "complete"})

        except Exception as e:
            self.queue.put({"type": "error", "content": str(e)})
        finally:
            self._thaw_system_prompt()
            # This reply's working time joins the session's (the cost log's
            # TIME field): wall-clock minus the dialog waits, as in MyAgent.
            if self._reply_started is not None:
                self._session_work_secs += max(
                    0.0, time.monotonic() - self._reply_started - self._input_wait_secs)
                self._reply_started = None

    def _tool_info(self, message):
        """Post a tool_info activity line to the GUI queue.

        check_queue renders it with the grey-italic "tool_info" tag and gates it on
        the Activity (show_activity) toggle. Every tool-dispatch path calls this;
        without it, executing any tool raised AttributeError: 'App' object has no
        attribute '_tool_info'.
        """
        self.queue.put({"type": "tool_info", "content": message})

    @staticmethod
    def _get_pricing(model_name, prompt_tokens=None):
        """Longest-prefix Anthropic pricing lookup → a per-token dict, or None. Uses
        MyAgent's ANTHROPIC_PRICING table (per-MTok 4-tuples: input, output,
        cache_write, cache_read) so the two apps' pricing stays in sync.
        ``prompt_tokens`` (2026-10-14; MyAgent's rule of 2026-10-08) is the
        call's whole prompt — the uncached input plus both cache buckets — and
        selects a model's SECOND rate card where it has one
        (ANTHROPIC_LONG_CONTEXT_PRICING: Haiku 5.5 above 100K tokens): the
        dict then carries that card's four rates plus ``long_context`` = the
        threshold crossed. None, or a prompt at or under the line, is the
        base card."""
        best, best_len = None, 0
        for prefix, prices in ANTHROPIC_PRICING.items():
            if model_name.startswith(prefix) and len(prefix) > best_len:
                best, best_len = prices, len(prefix)
        if best is None:
            return None
        pt = tuple(p / 1_000_000 for p in best)
        priced = {"input": pt[0], "output": pt[1], "cache_write": pt[2], "cache_read": pt[3]}
        if prompt_tokens is not None:
            card, card_len = None, 0
            for prefix, entry in ANTHROPIC_LONG_CONTEXT_PRICING.items():
                if model_name.startswith(prefix) and len(prefix) > card_len:
                    card, card_len = entry, len(prefix)
            if card is not None and prompt_tokens > card[0]:
                lr = tuple(p / 1_000_000 for p in card[1])
                priced = {"input": lr[0], "output": lr[1], "cache_write": lr[2],
                          "cache_read": lr[3], "long_context": card[0]}
        return priced

    def _get_model_param_summary(self):
        """Compact string of the parameters actually in effect — SelfBot's
        Anthropic-only mirror of MyAgent's ui_mixin helper, used for the cost
        log's params field. Adaptive models report their thinking mode (a stale
        "off" on an always-on Fable/Mythos model reports Adaptive, matching
        what _apply_thinking_params sends); manual models report the budget
        label; temperature appears only when it is actually sent."""
        parts = []
        support = self._model_supports_thinking()
        mode = (self.thinking_mode or "").lower()
        if support == "adaptive":
            if mode == "off" and self._is_always_on_thinking():
                parts.append("mode=Adaptive")
            else:
                parts.append(f"mode={mode.capitalize() or 'Off'}")
                if mode == "off" and not self._rejects_temperature():
                    parts.append(f"temp={self.temperature:g}")
        elif support == "manual" and self.thinking_enabled:
            label = next(
                (k for k, v in BUDGET_PRESETS.items() if v == self.thinking_budget),
                f"{self.thinking_budget}tok",
            )
            parts.append(f"thinking={label}")
        else:
            parts.append("thinking=off")
            if not self._rejects_temperature():
                parts.append(f"temp={self.temperature:g}")
        return " ".join(parts)

    # ── The cost log — MyAgent's collection, per session (2026-10-14) ──────
    # _log_api_cost, _cost_split_field, _run_progress_write and
    # _run_progress_clear are byte-identical copies of StreamingMixin's
    # (pinned in tests/test_selfbot_costlog.py); _run_progress_fold_in is
    # the same with SelfBot's wording; _session_log_args, _session_secs and
    # _run_progress_path are SelfBot's own — a session, not a run, is the
    # unit here, and the line is written by the close (_finish_close).
    # See CLAUDE_SELFBOT.md's API cost tracking paragraph.

    def _session_log_args(self):
        """The cost line's arguments from the live session — MyAgent's
        _log_run argument list, built in ONE place for the close
        (_finish_close → _log_api_cost) and the in-progress record
        (_run_progress_write, after every call) alike, so the two cannot
        drift: the session's cost and had_usage flag, its working seconds so
        far, the active saved system prompt's name as the INSTRUCTION field
        (SelfBot's analogue of an Agent Instruction; blank when none is
        loaded), the process's call total, the four token buckets, the
        per-model split and the stem of the chat file the close saved."""
        return dict(
            total_cost=self._session_cost,
            had_usage=self._session_had_usage,
            duration_secs=self._session_secs(),
            instruction=getattr(self, "system_prompt_name", "") or "",
            calls=self._session_calls,
            tokens=(self._session_tokens_in, self._session_tokens_out,
                    self._session_tokens_cache_write, self._session_tokens_cache_read),
            split=self._session_per_model,
            chat=getattr(self, "_last_saved_chat_name", "") or "")

    def _session_secs(self):
        """The session's working seconds — the cost log's TIME field: every
        finished reply's wall-clock minus the seconds it sat on a
        confirmation dialog (summed in stream_worker's finally), plus the
        reply in flight so far, measured the same way. The analogue of
        MyAgent's run clock: the model working, not the window-open time
        and not the human's typing between messages."""
        secs = self._session_work_secs
        if self._reply_started is not None:
            secs += max(0.0, time.monotonic() - self._reply_started - self._input_wait_secs)
        return secs

    @staticmethod
    def _cost_split_field(split, logged_model=None):
        """The cost log's 13th field (2026-09-27): a run's cost and token
        buckets per model that served its calls — "model,cost,in,out,
        cache_write,cache_read" per model, first-used first, joined by "|" —
        written when MORE than one model served the run (a Model upgrade
        from an Agent Request reply on, or an Anthropic refusal fallback
        served by an Opus tier), or when the ONE model that served it is not
        the row's MODEL field (`logged_model`: a run upgraded on its last
        reply, whose new model never served a priced call, is logged under the
        new model — without this field the viewers credited it the original
        model's whole cost). The viewers' By-model summaries split such a run
        between its models; every other block, and a line without the field,
        reads as before. "" when there is nothing to split, and then the
        field is not written at all. Cost at six places, so the parts sum to
        the row's four-place total; a model id's field separators, which no
        provider uses, are neutralised."""
        if not split:
            return ""
        if len(split) < 2 and (logged_model is None or logged_model in split):
            return ""
        parts = []
        for model, (cost, tok_in, tok_out, tok_cw, tok_cr) in split.items():
            name = str(model).replace(",", "_").replace("|", "_").replace(";", "_")
            parts.append(f"{name},{cost:.6f},{int(tok_in)},{int(tok_out)},"
                         f"{int(tok_cw)},{int(tok_cr)}")
        return "|".join(parts)

    def _log_api_cost(self, total_cost, had_usage=False, duration_secs=None,
                      instruction="", calls=None, tokens=None, split=None,
                      chat=None, provider=None, model=None, params=None,
                      timestamp=None):
        """Append the run's final cumulative cost to this machine's cost log.

        Called once when stream_worker's agentic loop ends (GUI and headless).
        Line format:
        {timestamp};{provider};{model};{cost};{params};{secs};{instruction};{calls}
        ;{in};{out};{cache_write};{cache_read}[;{split}[;{chat}]]
        — split (13th field, 2026-09-27, `_cost_split_field`) is present
        only for a run served by more than one model, or by one model other
        than the MODEL field, which is the model the run ENDED on;
        — chat (14th field, 2026-10-07) is the stem of the chat files the
        run's transcript is saved under — <chat>.json + .txt in CHATS_DIR,
        the saved_chats folder of the OneDrive share since 2026-10-08 (every
        machine's runs in one folder, so the file this names opens anywhere),
        the "Save Chat as" name, which every MyAgent run has since the same
        day (chat_mixin._name_run_chat) — whitespace-collapsed with any ';'
        turned into ',' like the instruction. When it is written the 13th
        field is ALWAYS present, blank for a one-model run, so a chat name
        can never be read as a split; a blank / None chat (SelfBot's caller,
        a bare host) keeps the 12-/13-field shapes exactly → the viewers'
        CHAT column, rightmost after INSTRUCTION;
        — params is the compact _get_model_param_summary() string
        (comma-joined), so the log records the thinking/temperature settings
        the run used alongside its cost; secs (6th field, 2026-08-12) is the
        run's wall-clock duration in whole seconds — minus any time the run
        sat waiting on user input (user_prompt / confirmation dialogs, since
        2026-08-13), so it measures the agent working, not the user's
        response latency — blank when duration_secs is None; instruction
        (7th field, 2026-08-16) is the name of the saved Agent Instruction
        the run was launched from (agent_instruction_name, snapshotted at
        run start — blank for an ad-hoc run), whitespace-collapsed and with
        any ';' turned into ',' so a name can never split the line; calls
        (8th field, 2026-08-16) is the run's API-call count — the "Call #N"
        counter in the output window, one per round-trip of the agentic loop,
        so every call after the first is a tool-use round-trip — blank when
        None. tokens (9th-12th fields, 2026-09-14) is the run's cumulative
        (input, output, cache_write, cache_read) token counts — the same four
        buckets _get_pricing rates, summed over every call of the agentic
        loop; they were already accumulated for the output window's running
        totals and merely discarded at log time, which left the log unable to
        answer "what is my blended $/MTok for this model?" (cost alone can't
        separate a 5x rate from a 40x cache-read discount). Written as plain
        integers, blank when tokens is None (SelfBot's caller, and any run
        whose provider returned no usage). input_tokens is the NON-cached
        count and the cache buckets are disjoint for EVERY provider — each
        normalizer subtracts a subset-style cache hit before emitting
        (OpenAI/Gemini since 2026-07-31, xAI/Kimi since 2026-09-15, when the
        gross pass-through was caught double-counting here), and Ollama's
        counts are summed although it is unpriced — so in+cache_write+cache_read
        is the run's true billed input volume — do not add input to a cache
        total expecting a subtotal. Older 4-/5-/6-/8-field lines stay valid
        and render blank PARAMETERS / TIME(sec) / INSTRUCTION / CALLS /
        TOKENS columns in the viewers.
        total_cost is the last cost displayed in the output window.
        Ollama runs are deliberately free but still logged (cost 0.0000) when
        at least one call returned usage (had_usage) — local activity shows in
        the viewers without inflating any spend total. Other zero-cost runs
        (a paid provider's unmatched model prefix, or a STOP before the first
        API result) stay skipped: a false $0.0000 line for a PAID provider
        would claim the run was free when the truth is the price is unknown.
        The log
        (APICOST_LOG_FILE via datapaths.resolve_costlog) is
        APICostLog_<machine>.txt in the OneDrive share — per-machine files
        never conflict-fork, yet every machine's spend syncs everywhere for
        the viewers to aggregate — falling back to repo-root APICostLog.txt
        on solo machines. Best-effort: any I/O failure is reported but never
        interrupts the run. Returns True when a line was written.
        provider / model / params / timestamp (2026-10-08) override the
        live fields for a line written on behalf of ANOTHER run — the
        in-progress record a killed instance left behind
        (_run_progress_fold_in): the record's own provider and model, its
        raw parameter summary (joined here like the live one) and the time
        of its last completed call, never this process's. None = the live
        value, so every existing caller writes exactly what it wrote."""
        provider = provider or self.provider
        model = model or self.model
        if not total_cost or total_cost <= 0:
            if not (provider == "Ollama" and had_usage):
                q = getattr(self, "queue", None)
                if had_usage and q is not None:
                    # A paid provider's run that made calls but could price
                    # none of them (no pricing row — the 2026-09-23 gpt-6-sol
                    # report): say so where the user looks for the line,
                    # rather than skipping in silence.
                    q.put({"type": "warning", "content": (
                        f"⚠ Run NOT written to the API cost log: {model} has no "
                        f"row in the {provider} pricing table, so its cost is "
                        f"unknown (a $0.0000 line would claim it was free). Add the "
                        f"row in myagent/constants.py.\n")})
                return False
            total_cost = 0.0
        try:
            timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 5th field: the title-bar parameter summary (e.g. "mode=Adaptive",
            # "reasoning=Medium temp=1"), comma-joined for log readability —
            # parts never contain ';' or ',' so the field can't split.
            summary = self._get_model_param_summary() if params is None else params
            params = ", ".join(str(summary).split())
            secs = "" if duration_secs is None else f"{duration_secs:.0f}"
            # 7th field: the instruction name is user-typed free text —
            # collapse whitespace (a stray newline would end the record
            # early) and neutralise the one character that could split it.
            instr = " ".join(str(instruction or "").split()).replace(";", ",")
            calls_s = "" if calls is None else f"{int(calls)}"
            # 9th-12th fields: the run's cumulative token buckets. All four
            # are blank together when tokens is None — a partially-filled
            # TOKENS group would read as "zero tokens billed" in the viewers
            # rather than "not recorded".
            tok_s = (";;;" if not tokens
                     else ";".join(f"{int(t)}" for t in tokens))
            # 13th field: the per-model split, only for a run more than one
            # model served (or one model other than the MODEL field) — a line
            # its MODEL field describes keeps its 12 fields exactly.
            split_s = self._cost_split_field(split, model)
            # 14th field (2026-10-07): the chat file stem, sanitised like the
            # instruction. It forces the 13th out, blank or not, so a reader
            # finds the split at 13 and the chat at 14 whatever the run did.
            chat_s = " ".join(str(chat or "").split()).replace(";", ",")
            if chat_s:
                tail = f";{split_s};{chat_s}"
            else:
                tail = f";{split_s}" if split_s else ""
            # ';' delimiter (not ',') so a comma inside a model name, the
            # params field or an instruction name can't be misread as a
            # field separator.
            line = (f"{timestamp};{provider};{model};"
                    f"{total_cost:.4f};{params};{secs};{instr};{calls_s};"
                    f"{tok_s}{tail}\n")
            rotate_log_if_needed(APICOST_LOG_FILE, APICOST_LOG_MAX_BYTES)
            # newline="\n": the per-machine logs are read cross-platform via
            # OneDrive; Windows text-mode CRLF shows as ^M in the macOS viewer.
            with open(APICOST_LOG_FILE, "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
            self._tool_info(f"Logged API cost to {APICOST_LOG_FILE}: {line}")
            return True
        except Exception as e:
            self.queue.put({"type": "warning",
                            "content": f"⚠ Could not write the API cost log "
                                       f"({APICOST_LOG_FILE}): {e}\n"})
            return False

    def _run_progress_path(self):
        """selfbot_run_<1|2>.json for this instance — None on a host without
        a state file (the bare test hosts), which therefore never writes
        one: the real App always has _state_file, and a test harness run
        beside a live instance must not touch that instance's record."""
        if not getattr(self, "_state_file", None):
            return None
        num = 2 if getattr(self, "_is_second_instance", False) else 1
        return f"{SELFBOT_RUN_PREFIX}{num}.json"

    def _run_progress_write(self, total_cost, had_usage, duration_secs,
                            instruction="", calls=None, tokens=None, split=None,
                            chat=None):
        """Rewrite this instance's in-progress cost record: the exact
        arguments _log_run would hand _log_api_cost if the run ended now,
        plus the live provider / model / parameter summary and the time, so
        the line can be written later by a process that knows nothing else
        about the run. stream_worker calls it after every call's cost
        accounting (on the worker — file IO only, no Tk), and _end_run
        removes the record after the real line on both tails, so a record
        found at launch means its instance died with a run in flight: a
        reboot while parked at an Agent Request (the 2026-10-08 case — a
        US$7.77 run with a complete transcript and no line), a taskkill, a
        power cut. Atomic (mkstemp + os.replace) so a kill mid-write leaves
        the previous record, never a torn one. Best-effort: a failure is
        reported once per run and never interrupts it."""
        path = self._run_progress_path()
        if path is None:
            return
        try:
            record = {
                "version": 1,
                "written": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "provider": self.provider,
                "model": self.model,
                "params": self._get_model_param_summary(),
                "total_cost": total_cost,
                "had_usage": bool(had_usage),
                "duration_secs": duration_secs,
                "instruction": instruction or "",
                "calls": calls,
                "tokens": list(tokens) if tokens else None,
                "split": split or {},
                "chat": chat or "",
            }
            fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".",
                                       suffix=".tmp", dir=os.path.dirname(path))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(record, f, ensure_ascii=False)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:
            if not getattr(self, "_run_progress_warned", False):
                self._run_progress_warned = True
                self.queue.put({"type": "warning", "content": (
                    f"⚠ Could not write the in-progress cost record ({path}): {e}\n")})

    def _run_progress_clear(self):
        """Remove this instance's in-progress record: the run ended and
        _log_run has written (or deliberately skipped) its line. Called by
        stream_worker's _end_run on both tails, AFTER _log_run."""
        self._run_progress_warned = False
        path = self._run_progress_path()
        if path is None:
            return
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            self.queue.put({"type": "warning", "content": (
                f"⚠ Could not remove the in-progress cost record ({path}): {e}\n")})

    def _run_progress_fold_in(self):
        """At launch, once this process knows its instance slot (SelfBot's
        __init__): a record the slot's previous owner left behind means that
        instance died with a run in flight — killed from outside, since both
        loop-end tails remove the record after the line — so its cost line is
        written NOW from the record: under the record's own provider / model
        / parameters, stamped with the time of its last completed call, with
        `unfinished@call<N>` appended to PARAMETERS (the viewers show it in
        that column; the cost counts, it was spent). The zero-cost gate
        applies as it would have at the run's end. The record is removed
        whatever happens (an unreadable one is reported, not retried at every
        launch). Returns True when a line was written."""
        path = self._run_progress_path()
        if path is None or not os.path.exists(path):
            return False
        written = False
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            if not isinstance(rec, dict):
                raise ValueError("not a JSON object")
            calls = rec.get("calls")
            marker = f"unfinished@call{int(calls)}" if calls else "unfinished"
            summary = str(rec.get("params") or "")
            tokens = rec.get("tokens")
            written = self._log_api_cost(
                float(rec.get("total_cost") or 0.0), bool(rec.get("had_usage")),
                rec.get("duration_secs"),
                instruction=rec.get("instruction") or "", calls=calls,
                tokens=tuple(tokens) if tokens else None,
                split=rec.get("split") or None, chat=rec.get("chat") or "",
                provider=rec.get("provider") or None,
                model=rec.get("model") or None,
                params=f"{summary} {marker}".strip(),
                timestamp=rec.get("written") or None)
            if written:
                self.queue.put({"type": "warning", "content": (
                    f"⚠ The previous session of this SelfBot instance ended without "
                    f"its cost line (it was killed with calls unlogged — chat "
                    f"{rec.get('chat') or '?'}): logged now as {marker}.\n")})
        except Exception as e:
            self.queue.put({"type": "warning", "content": (
                f"⚠ Could not fold in the in-progress cost record ({path}): {e}\n")})
        self._run_progress_clear()
        return written
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
                    if call_cost is None:
                        # An unpriced call (no pricing row for the model): the
                        # tokens alone, in MyAgent's words.
                        self._chat_insert(
                            (f"  unpriced this call  |  no pricing row  ({token_str})\n",
                             "cost_info"))
                    else:
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
                    # Inject response body into the other instance's input
                    if self._current_response_text.strip() and self._auto_chat.get():
                        self._pending_injection = False
                        self._pass_pending = True   # on its way: a message typed here waits
                        # The first reply takes the opener typed here
                        # along, just ahead of it (either instance).
                        if self._response_count == 1:
                            self._write_inject_file()
                        self.root.after(1000, self._inject_response_to_other)
                    else:
                        if self._current_response_text.strip():
                            self._pending_injection = True   # Auto off: held for its release
                        self._set_busy(False)   # nothing goes to the partner now
                    self._focus_for_reply(bell=True)
                elif msg["type"] == "error":
                    self._chat_insert((f"Error: {msg['content']}\n\n", "error"))
                    self.streaming = False
                    self._set_busy(False)   # the turn ended with no answer to hand over
                    self._focus_for_reply(bell=True)
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
