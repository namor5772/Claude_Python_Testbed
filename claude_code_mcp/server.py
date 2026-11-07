#!/usr/bin/env python
"""MyAgent's tool catalogue as a Model Context Protocol server for Claude Code.

Nothing in MyAgent itself is edited, installed or configured by this file —
it lives in its own folder of the repository, imports the `myagent` package
read-only (the repository is this file's parent folder, or MYAGENT_REPO) and
builds a headless host from the SAME mixins MyAgent's App class is made of,
the way the characterization suite's `tests/_util.py` does. Every tool call
goes through MyAgent's own dispatcher, `StreamingMixin._run_tool`, so the
behaviour is MyAgent's; the handful of methods that need a Tk window are
overridden here, on the subclass, never in the package:

  _request_confirmation / _confirm_*_action — the Tk confirmation dialogs.
      No dialog can open here, and Claude Code may be running with its own
      permission prompts bypassed, so by DEFAULT a confirm is refused and
      the result says why (Claude Code's own Bash tool covers the command
      case). Register with -e MYAGENT_MCP_CONFIRM=allow to grant them; the
      result then carries a note naming the pattern MyAgent would have asked
      about. COMMAND_BLOCKED patterns stay blocked either way (that gate is
      in run_powershell itself).
  do_user_prompt / do_upgrade_model — controls of a MyAgent RUN, not tools
      over the world; left out of the catalogue and answered with guidance
      if called anyway.
  do_clipboard_read / do_clipboard_write — Tk's clipboard; the platform's
      clipboard commands here.
  _post_skill_ui_refresh — refreshes an open Skills Manager; nothing to do.

Catalogue: `_get_tools()` evaluated with every tool family enabled (the
Google provider keeps every tool in the list, including the local web pair
the Anthropic path strips), minus `find_element` (Gemini's pointing API,
useless to Claude), `user_prompt` and `upgrade_model`. MyAgent's own MCP
servers (mcp_servers.json) are not chained through — register those in
Claude Code directly.

Run: <venv python> server.py        (Claude Code spawns it; stdio transport)
Register once (from the repository root):
  claude mcp add --scope user myagent -- "$PWD/.venv/bin/python" "$PWD/claude_code_mcp/server.py"
"""
import asyncio
import concurrent.futures
import io
import os
import queue
import subprocess
import sys

REPO = os.environ.get("MYAGENT_REPO") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

# --- stdout hygiene: the protocol owns the real stdout; everything else
# (prints, child processes such as Edge or a MyAgent child run) goes to
# stderr, which Claude Code keeps in its MCP log.
_PROTOCOL_FD = os.dup(1)
os.dup2(2, 1)
sys.stdout = sys.stderr


def log(msg):
    print(f"[myagent-mcp] {msg}", file=sys.stderr, flush=True)


import anyio  # noqa: E402
import mcp.types as types  # noqa: E402
from mcp.server.lowlevel import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402

from myagent.helpers import _ToolBlock  # noqa: E402
from myagent.ui_mixin import UIMixin  # noqa: E402
from myagent.state_mixin import StateMixin  # noqa: E402
from myagent.instructions_mixin import InstructionsMixin  # noqa: E402
from myagent.skills_mixin import SkillsMixin  # noqa: E402
from myagent.streaming_mixin import StreamingMixin  # noqa: E402
from myagent.anthropic_mixin import AnthropicMixin  # noqa: E402
from myagent.openai_mixin import OpenAIMixin  # noqa: E402
from myagent.gemini_mixin import GeminiMixin  # noqa: E402
from myagent.xai_mixin import XAIMixin  # noqa: E402
from myagent.kimi_mixin import KimiMixin  # noqa: E402
from myagent.ollama_mixin import OllamaMixin  # noqa: E402
from myagent.mcp_mixin import MCPMixin  # noqa: E402
from myagent.gmail_mixin import GmailMixin  # noqa: E402
from myagent.protonmail_mixin import ProtonMailMixin  # noqa: E402
from myagent.outlook_mixin import OutlookMixin  # noqa: E402
from myagent.document_mixin import DocumentMixin  # noqa: E402
from myagent.file_mixin import FileMixin  # noqa: E402
from myagent.desktop_mixin import DesktopMixin  # noqa: E402
from myagent.browser_mixin import BrowserMixin  # noqa: E402
from myagent.excel_mixin import ExcelMixin  # noqa: E402
from myagent.physical_mixin import PhysicalMixin  # noqa: E402
from myagent.safety_mixin import SafetyMixin  # noqa: E402
from myagent.voice_mixin import VoiceMixin  # noqa: E402
from myagent.model_upgrade_mixin import ModelUpgradeMixin  # noqa: E402
from myagent.other_setup_mixin import OtherSetupMixin  # noqa: E402
from myagent.chat_mixin import ChatMixin  # noqa: E402
from myagent.event_loop_mixin import EventLoopMixin  # noqa: E402
from myagent.constants import (  # noqa: E402
    DEFAULT_SYSTEM_PROMPT, DEFAULT_INSTRUCTION, OLLAMA_DEFAULT_BASE_URL,
)

SERVER_NAME = "myagent"
EXCLUDED_TOOLS = ("find_element", "user_prompt", "upgrade_model")
CONFIRM_POLICY = (os.environ.get("MYAGENT_MCP_CONFIRM") or "deny").strip().lower()


class _Flag:
    """Stands in for a tk.BooleanVar: the mixins only ever .get() it."""

    def __init__(self, value):
        self._value = bool(value)

    def get(self):
        return self._value

    def set(self, value):
        self._value = bool(value)


def _clipboard_commands():
    if sys.platform == "darwin":
        return ["pbpaste"], ["pbcopy"]
    if sys.platform == "win32":
        return (["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                ["clip"])
    return ["xclip", "-selection", "clipboard", "-o"], ["xclip", "-selection", "clipboard"]


class Host(UIMixin, StateMixin, InstructionsMixin, SkillsMixin,
           StreamingMixin, AnthropicMixin, OpenAIMixin, GeminiMixin,
           XAIMixin, KimiMixin, OllamaMixin, MCPMixin, GmailMixin,
           ProtonMailMixin, OutlookMixin, DocumentMixin, FileMixin,
           DesktopMixin, BrowserMixin, ExcelMixin, PhysicalMixin, SafetyMixin,
           VoiceMixin, ModelUpgradeMixin, OtherSetupMixin, ChatMixin, EventLoopMixin):
    """MyAgent's App without its window: the same MRO, the attributes the
    tools read, and the Tk-bound methods replaced."""

    def __init__(self):
        self.root = None                 # every `getattr(self, "root", None)` path runs inline
        self._headless = False           # a browser OAuth sign-in may open: someone is at the keyboard
        self._result_file = None
        self._relay_dir = None
        self._relay_seq = 0
        self._extra_text = ""
        self._prepend_text = ""
        self.queue = queue.Queue()       # _tool_info / _warn land here; drained after each call
        self.messages = []
        self.streaming = False
        self.stop_requested = False
        self.pending_images = []
        self._editor_images = []
        self._prompt_attached_images = []
        self._turn_text = ""
        # The screenshot pipeline's coordinate state, as App.__init__ seeds it.
        self._screenshot_scale = 1.0
        self._screenshot_offset = (0, 0)
        self._screenshot_dims = (0, 0)
        self._display_states = {}
        self._display_images = {}
        self._display_full_states = {}
        self._display_full_images = {}
        self._last_screenshot_bytes = None
        # Every tool family on; MyAgent's own MCP client off (not chained).
        self.desktop_enabled = _Flag(True)
        self.browser_enabled = _Flag(True)
        self.excel_enabled = _Flag(True)
        self.physical_enabled = _Flag(True)
        self.meta_enabled = _Flag(True)
        self.google_enabled = _Flag(True)
        self.proton_enabled = _Flag(True)
        self.outlook_enabled = _Flag(True)
        self.mcp_enabled = _Flag(False)
        self.conversational_enabled = _Flag(False)
        self.dictation_auto_send = _Flag(False)
        self.announce_request = _Flag(False)
        self.debug_enabled = _Flag(False)
        self.show_activity = _Flag(False)
        self._disabled_confirm_patterns = set()
        self._blocked_tools = set()
        self._playwright = None
        self._browser = None
        self._page = None
        self._edge_process = None
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        self._run_system_prompt = None
        # The consumer of every image is Claude, so screenshots are sized by
        # the Anthropic caps (1568 px / 1.15 MP) and the coordinate scale
        # matches what the model sees.
        self.provider = "Anthropic"
        self.model = "claude-fable-5-1"
        self.temperature = 1.0
        self.thinking_enabled = False
        self.thinking_effort = "high"
        self.thinking_budget = 8192
        self._anthropic_output_caps = {}
        self._anthropic_unsupported = set()
        self._anthropic_no_temperature = set()
        # The clients microphone_listen's transcription may use (Voice Setup
        # decides which); each only when its key is in the environment.
        self.client = self.openai_client = self.gemini_client = self.xai_client = None
        try:
            if os.environ.get("OPENAI_API_KEY"):
                import openai
                self.openai_client = openai.OpenAI()
            if os.environ.get("XAI_API_KEY"):
                import openai
                from myagent.constants import XAI_DEFAULT_BASE_URL
                self.xai_client = openai.OpenAI(api_key=os.environ["XAI_API_KEY"],
                                                base_url=XAI_DEFAULT_BASE_URL)
            key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if key:
                from google import genai
                self.gemini_client = genai.Client(api_key=key)
        except Exception as e:  # a client is a convenience for one tool, never fatal
            log(f"client setup skipped: {e}")
        # The per-mixin state the App constructor seeds the same way.
        self._init_mcp_state()
        self._google_init_state()
        self._proton_init_state()
        self._outlook_init_state()
        # Run / editor state a tool path may read; the values of a fresh App.
        self.agent_instruction_name = ""
        self.agent_instruction_notes = ""
        self.instruction_editor_window = None
        self.skills_editor_window = None
        self._skills_list = None
        self._skills_refresh_list = None
        self.upgrade_target = None
        self._upgrade_original = None
        self._run_chat_name = ""
        self._run_call_num = 0
        self.fast_mode = False
        self.thinking_mode = "off"
        self.text_verbosity = "medium"
        self.kimi_client = self.ollama_client = None
        self.available_models = []
        self.diag_enabled = _Flag(False)
        self.tool_calls_enabled = _Flag(False)
        self.show_thinking = _Flag(False)
        self.save_thinking = _Flag(False)
        self.agent_instruction = DEFAULT_INSTRUCTION
        self._launch_instruction = None
        self._show_window = False
        self._instance_num = 0
        self._state_file = None
        self._auto_chat_name = None
        self._current_response_text = ""
        self._current_thinking_text = ""
        self._collapsed_sections = set()
        self._skills_collapsed_sections = set()
        self._instr_list_width = None
        self._skill_desc_text = ""
        self._desc_dialog = None
        self._desc_text = None
        self._skills_name_entry = None
        self.theme_bg = self.theme_field = self.theme_button = None
        self._has_anthropic = bool(os.environ.get("ANTHROPIC_API_KEY"))
        self._has_openai = bool(os.environ.get("OPENAI_API_KEY"))
        self._gemini_api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self._has_gemini = bool(self._gemini_api_key)
        self._has_xai = bool(os.environ.get("XAI_API_KEY"))
        self._kimi_api_key = os.environ.get("MOONSHOT_API_KEY") or os.environ.get("KIMI_API_KEY")
        self._has_kimi = bool(self._kimi_api_key)
        self._has_ollama = False
        self._ollama_base_url = os.environ.get("OLLAMA_BASE_URL", OLLAMA_DEFAULT_BASE_URL)
        self._model_display_names = {}
        self._openai_model_display_names = {}
        self._gemini_model_display_names = {}
        self._xai_model_display_names = {}
        self._kimi_model_display_names = {}
        self._ollama_model_display_names = {}
        self._xai_caps = {}
        self._openai_unsupported_tools = {}
        self._responses_replay_off = set()
        self._kimi_reasoning_rejected = set()
        self.skills = self._load_skills()

    # --- Tk-bound methods replaced -----------------------------------------

    def _confirm_policy(self, label, detail):
        """MyAgent's confirmation dialog, decided by policy. Default DENY:
        Claude Code may be running with permissions bypassed, and then an
        auto-granted confirm would be no gate at all. Register the server
        with -e MYAGENT_MCP_CONFIRM=allow to grant them (Claude Code's own
        per-call prompt then being the gate)."""
        if CONFIRM_POLICY != "allow":
            self._warn(f"⚠ {label} refused ({detail}): MyAgent would ask for confirmation "
                       "here and no dialog can open in Claude Code. For a command, use "
                       "Claude Code's own Bash tool; to let this server grant confirms, "
                       "register it with -e MYAGENT_MCP_CONFIRM=allow\n")
            return False
        self._warn(f"⚠ {label} granted without MyAgent's dialog ({detail}); "
                   "Claude Code's permission prompt is the gate here\n")
        return True

    def _request_confirmation(self, command, matched_pattern=""):
        return self._confirm_policy("command confirm", f"pattern {matched_pattern!r}")

    def _confirm_gmail_action(self, tool_name, title, summary, detail):
        return self._confirm_policy(f"Gmail {tool_name}", summary)

    def _confirm_proton_action(self, tool_name, title, summary, detail):
        return self._confirm_policy(f"Proton {tool_name}", summary)

    def _confirm_outlook_action(self, tool_name, title, summary, detail):
        return self._confirm_policy(f"Outlook {tool_name}", summary)

    def do_user_prompt(self, message, convo=False, preface=""):
        return ("[No Agent Request dialog in Claude Code — ask the user directly in the "
                f"conversation instead. The question was: {message}]")

    def do_upgrade_model(self, params=None):
        return "upgrade_model applies to a MyAgent run; it has no effect in Claude Code."

    def _post_skill_ui_refresh(self):
        return None

    def do_clipboard_read(self):
        read_cmd, _ = _clipboard_commands()
        try:
            out = subprocess.run(read_cmd, capture_output=True, text=True, timeout=10)
            text = out.stdout
            if out.returncode != 0 or not text:
                return "Clipboard is empty or contains non-text data."
            return f"Clipboard contents:\n{text}"
        except Exception as e:
            return f"Clipboard read error: {e}"

    def do_clipboard_write(self, text):
        _, write_cmd = _clipboard_commands()
        try:
            subprocess.run(write_cmd, input=text, text=True, timeout=10, check=True)
            preview = text[:100] + "..." if len(text) > 100 else text
            return f"Copied to clipboard ({len(text)} chars): {preview}"
        except Exception as e:
            return f"Clipboard write error: {e}"

    # --- the catalogue -------------------------------------------------------

    def catalogue(self):
        """MyAgent's current tool list, every family on. `_get_tools` keeps
        every tool only for the Google provider (the Anthropic path strips
        the local web pair), so it is evaluated as Google and the provider
        put back for execution."""
        saved = self.provider
        self.provider = "Google"
        try:
            tools = self._get_tools()
        finally:
            self.provider = saved
        return [t for t in tools if t["name"] not in EXCLUDED_TOOLS]


# --- the MCP server ----------------------------------------------------------

HOST = Host()
WORKER = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="myagent-tools")
server = Server(SERVER_NAME)


def _drain_notes():
    warnings, infos = [], []
    while True:
        try:
            msg = HOST.queue.get_nowait()
        except queue.Empty:
            break
        kind = msg.get("type") if isinstance(msg, dict) else None
        text = (msg.get("content") or "").rstrip("\n") if isinstance(msg, dict) else str(msg)
        if kind == "warning":
            warnings.append(text)
        elif kind == "tool_info":
            infos.append(text)
    for line in infos:
        log(line)
    return warnings


def _to_content(result, warnings):
    """MyAgent's tool result — a string, or the [text, image, ...] block list
    the screenshot / camera / view_image tools return — as MCP content."""
    out = []
    if isinstance(result, list):
        for block in result:
            if not isinstance(block, dict):
                out.append(types.TextContent(type="text", text=str(block)))
            elif block.get("type") == "image":
                src = block.get("source") or {}
                out.append(types.ImageContent(type="image", data=src.get("data", ""),
                                              mimeType=src.get("media_type", "image/png")))
            else:
                out.append(types.TextContent(type="text", text=str(block.get("text", ""))))
    else:
        out.append(types.TextContent(type="text", text="" if result is None else str(result)))
    if warnings:
        out.append(types.TextContent(type="text", text="MyAgent notes:\n" + "\n".join(warnings)))
    return out


def _run_one(name, arguments):
    HOST.stop_requested = False
    block = _ToolBlock(name, f"mcp_{name}", arguments or {})
    result = HOST._run_tool(block)      # MyAgent's dispatcher, crash guard included
    return _to_content(result, _drain_notes())


@server.list_tools()
async def list_tools():
    loop = asyncio.get_running_loop()
    tools = await loop.run_in_executor(WORKER, HOST.catalogue)
    _drain_notes()
    return [types.Tool(name=t["name"], description=t.get("description", ""),
                       inputSchema=t["input_schema"]) for t in tools]


@server.call_tool(validate_input=False)   # every do_* validates and answers in words, as in MyAgent
async def call_tool(name, arguments):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(WORKER, _run_one, name, arguments)


async def main():
    protocol_out = anyio.wrap_file(io.TextIOWrapper(os.fdopen(_PROTOCOL_FD, "wb"), encoding="utf-8"))
    log(f"serving MyAgent tools from {REPO} (confirm policy: {CONFIRM_POLICY})")
    async with stdio_server(stdout=protocol_out) as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def _shutdown():
    try:
        if HOST._page is not None or HOST._browser is not None:
            HOST.do_browser_close()
    except Exception:
        pass
    WORKER.shutdown(wait=False, cancel_futures=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    finally:
        _shutdown()
