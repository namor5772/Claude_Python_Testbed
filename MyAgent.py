import sys
IS_WINDOWS = sys.platform == "win32"

import ctypes
if IS_WINDOWS:
    # DPI awareness for desktop automation. Must run before any window creation.
    # Prefer PER_MONITOR_AWARE_V2 (DPI_AWARENESS_CONTEXT = -4) over v1:
    # v1 PROCESS_PER_MONITOR_DPI_AWARE is broken for multi-monitor setups with
    # mixed DPIs — a 2560x1440 @ 225% secondary next to a 100% primary reports
    # as ~1138x640 in EnumDisplayMonitors under v1, causing ImageGrab to return
    # a low-res image and mouse clicks to land in the wrong place. V2 gives
    # consistent physical pixels across all monitors regardless of per-monitor DPI.
    # V2 is available on Windows 10 1703+ and Windows 11.
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
from tkinter import messagebox
import anthropic
import openai
from google import genai
import httpx
import queue
import os
import urllib.request

from myagent.constants import (
    DEFAULT_GEOMETRY, DEFAULT_MODEL, DEFAULT_SYSTEM_PROMPT, DEFAULT_INSTRUCTION,
    OPENAI_DEFAULT_MODEL, GEMINI_DEFAULT_MODEL, XAI_DEFAULT_MODEL,
    XAI_DEFAULT_BASE_URL, OLLAMA_DEFAULT_MODEL,
    OLLAMA_DEFAULT_BASE_URL, _HAS_OLLAMA, _HAS_MCP, AGENT_STATE_FILE, _BASE_DIR,
)
from myagent.ui_mixin import UIMixin
from myagent.state_mixin import StateMixin
from myagent.instructions_mixin import InstructionsMixin
from myagent.skills_mixin import SkillsMixin
from myagent.streaming_mixin import StreamingMixin
from myagent.anthropic_mixin import AnthropicMixin
from myagent.openai_mixin import OpenAIMixin
from myagent.gemini_mixin import GeminiMixin
from myagent.xai_mixin import XAIMixin
from myagent.ollama_mixin import OllamaMixin
from myagent.mcp_mixin import MCPMixin
from myagent.gmail_mixin import GmailMixin
from myagent.protonmail_mixin import ProtonMailMixin
from myagent.outlook_mixin import OutlookMixin
from myagent.document_mixin import DocumentMixin
from myagent.file_mixin import FileMixin
from myagent.desktop_mixin import DesktopMixin
from myagent.browser_mixin import BrowserMixin
from myagent.safety_mixin import SafetyMixin
from myagent.chat_mixin import ChatMixin
from myagent.event_loop_mixin import EventLoopMixin

if _HAS_OLLAMA:
    import ollama


# ── Main Application ────────────────────────────────────────────────────────

class App(UIMixin, StateMixin, InstructionsMixin, SkillsMixin,
          StreamingMixin, AnthropicMixin, OpenAIMixin, GeminiMixin,
          XAIMixin, OllamaMixin, MCPMixin, GmailMixin, ProtonMailMixin,
          OutlookMixin, DocumentMixin, FileMixin, DesktopMixin, BrowserMixin,
          SafetyMixin, ChatMixin, EventLoopMixin):

    def __init__(self, root, launch_instruction=None, headless=False, result_file=None):
        self.root = root
        self._headless = headless
        # Optional path (from --result-file) where stream_worker writes a JSON
        # summary of the run's outcome on completion — the return channel for a
        # parent agent's blocking run_instruction(wait=true).
        self._result_file = result_file
        self.root.title("My Agent")
        self.root.geometry(DEFAULT_GEOMETRY)

        # Check for at least one API key
        self._has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
        self._has_openai = bool(os.environ.get("OPENAI_API_KEY"))
        self._has_gemini = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        self._has_xai = bool(os.environ.get("XAI_API_KEY"))
        # Ollama needs no API key — availability = local server responds on the
        # base URL. A quick HEAD on /api/tags with a short timeout catches the
        # common "Ollama not running" case without blocking UI init if the user
        # has Ollama installed but stopped.
        self._has_ollama = False
        self._ollama_base_url = os.environ.get("OLLAMA_BASE_URL", OLLAMA_DEFAULT_BASE_URL)
        if _HAS_OLLAMA:
            try:
                with urllib.request.urlopen(f"{self._ollama_base_url}/api/tags", timeout=0.5):
                    self._has_ollama = True
            except Exception:
                self._has_ollama = False
        if not (self._has_anthropic or self._has_openai or self._has_gemini
                or self._has_xai or self._has_ollama):
            messagebox.showerror(
                "API Key Missing",
                "Please set at least one of ANTHROPIC_API_KEY, OPENAI_API_KEY, "
                "GEMINI_API_KEY, or XAI_API_KEY — or start a local Ollama server.",
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
        # xAI is OpenAI-compatible — same SDK, different base URL and key.
        self.xai_client = openai.OpenAI(
            api_key=os.environ.get("XAI_API_KEY"),
            base_url=XAI_DEFAULT_BASE_URL,
            timeout=httpx.Timeout(600.0, connect=10.0, read=120.0),
        ) if self._has_xai else None
        # 300s read timeout (max silent gap between stream chunks): tolerates a
        # cold model load, but a request stuck in Ollama's queue behind a wedged
        # instance raises instead of blocking forever (default is no timeout).
        self.ollama_client = ollama.Client(
            host=self._ollama_base_url,
            timeout=httpx.Timeout(300.0, connect=10.0),
        ) if self._has_ollama else None
        if self._has_anthropic:
            self.provider = "Anthropic"
        elif self._has_openai:
            self.provider = "OpenAI"
        elif self._has_gemini:
            self.provider = "Gemini"
        elif self._has_xai:
            self.provider = "xAI"
        else:
            self.provider = "Ollama"
        self._openai_model_display_names = {}
        self._gemini_model_display_names = {}
        self._xai_model_display_names = {}
        self._ollama_model_display_names = {}
        self._model_display_names = {}
        # Per-model cache of OpenAI server-side tools that the model rejected
        # with a 400 BadRequestError. Newer models like gpt-5.2-pro don't support
        # code_interpreter; older models did. The first call learns, subsequent
        # calls in the same session skip the unsupported tool upfront.
        self._openai_unsupported_tools = {}  # model_id → set of unsupported "type" strings
        # Anthropic models (Opus 4.7+) that removed temperature/top_p/top_k and 400
        # if sent. Same learn-once-skip-after pattern as the OpenAI cache above.
        self._anthropic_no_temperature = set()  # model_ids that rejected temperature
        self.messages = []
        self.queue = queue.Queue()
        self.streaming = False
        self.stop_requested = False
        self.pending_images = []   # list of (base64_data, media_type, filename)
        self._editor_images = []   # working copy while editor is open
        self._screenshot_scale = 1.0
        self._screenshot_offset = (0, 0)  # display origin offset for per-display screenshots
        self._screenshot_dims = (0, 0)    # (width, height) of last screenshot sent to model
        # Per-display "most recent capture" — updated on BOTH full and region
        # captures. Used by mouse_click and find_element so the model can click
        # using coordinates from whatever it most recently saw of that display
        # (full screen or zoomed region).
        self._display_states = {}         # display_idx → (scale, (ox, oy), (w, h))
        self._display_images = {}         # display_idx → raw PNG bytes (pre-grid)
        # Per-display "full display state" — updated ONLY on full-display captures.
        # Used by region screenshots: when the model says "screenshot display=1
        # region (880, 250, 320, 160)", those coordinates are relative to display
        # 1's FULL image, not relative to whatever region was captured most recently.
        # Without this separation, chained region screenshots on the same display
        # would drift through stacked offsets.
        self._display_full_states = {}    # display_idx → (scale, (ox, oy), (w, h))
        self._display_full_images = {}    # display_idx → raw PNG bytes
        self._last_screenshot_bytes = None  # raw resized image bytes (pre-PNG) — fallback for find_element when no display specified
        self.debug_enabled = tk.BooleanVar(value=False)
        self.tool_calls_enabled = tk.BooleanVar(value=False)
        self.show_activity = tk.BooleanVar(value=False)
        self.show_thinking = tk.BooleanVar(value=False)
        self.save_thinking = tk.BooleanVar(value=False)
        # Independent toggle for [DIAG] capture/click trace logging — separate
        # from Debug (which shows the API JSON payload). Defaults ON for now;
        # uncheck "Diag" in the main window to silence the diagnostic lines.
        self.diag_enabled = tk.BooleanVar(value=True)
        self.desktop_enabled = tk.BooleanVar(value=False)
        self.browser_enabled = tk.BooleanVar(value=False)
        self.meta_enabled = tk.BooleanVar(value=False)
        self.mcp_enabled = tk.BooleanVar(value=False)
        self._init_mcp_state()
        self.google_enabled = tk.BooleanVar(value=False)
        self._google_init_state()
        self.proton_enabled = tk.BooleanVar(value=False)
        self._proton_init_state()
        self.outlook_enabled = tk.BooleanVar(value=False)
        self._outlook_init_state()
        # Conversational mode: when on, MyAgent enforces a chatbot loop by
        # invoking do_user_prompt directly whenever the model ends a turn
        # without calling user_prompt itself. Useful for smaller models that
        # don't reliably follow "always call user_prompt" meta-rules.
        self.conversational_enabled = tk.BooleanVar(value=False)
        self._disabled_confirm_patterns = set()
        # Per-instruction hard tool blocklist: names here are stripped from the
        # offered tools AND refused at _execute_tool dispatch — a deterministic
        # guarantee for unattended runs, independent of model compliance.
        self._blocked_tools = set()
        self._playwright = None
        self._browser = None
        self._page = None
        self._edge_process = None
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        if self.provider == "Anthropic":
            self.model = DEFAULT_MODEL
        elif self.provider == "OpenAI":
            self.model = OPENAI_DEFAULT_MODEL
        elif self.provider == "Gemini":
            self.model = GEMINI_DEFAULT_MODEL
        elif self.provider == "xAI":
            self.model = XAI_DEFAULT_MODEL
        else:
            self.model = OLLAMA_DEFAULT_MODEL
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

        # Connect MCP servers after the UI is up so a slow stdio handshake never
        # blocks app launch. The mixin no-ops if no mcp_servers.json exists.
        if _HAS_MCP:
            self.root.after(100, self._connect_mcp_servers)

        if self._headless:
            self.root.withdraw()

        # Auto-launch instruction from -l command-line argument
        self._launch_instruction = launch_instruction
        if self._launch_instruction:
            self.root.after(100, self._auto_launch)


if __name__ == "__main__":
    import argparse
    import signal
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    parser = argparse.ArgumentParser(description="My Agent — autonomous task runner")
    parser.add_argument("-l", "--load", metavar="NAME",
                        help="Load an instruction by name and auto-start the agent")
    parser.add_argument("--headless", action="store_true",
                        help="Run without main window (dialogs still shown when needed)")
    parser.add_argument("--result-file", metavar="PATH",
                        help="Write a JSON result summary (status + final assistant text) "
                             "to PATH when the agentic loop ends — used by a parent "
                             "agent's run_instruction(wait=true)")
    args = parser.parse_args()
    root = tk.Tk()
    app = App(root, launch_instruction=args.load, headless=args.headless,
              result_file=args.result_file)
    root.mainloop()
