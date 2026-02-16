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
import subprocess
import re


# Tool definitions for the Anthropic API
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
]

# PowerShell safety guardrails — two-tier system
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

FALLBACK_MODELS = [
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
]
DEFAULT_MODEL = FALLBACK_MODELS[0]

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant with access to web_search and fetch_webpage tools. "
    "When the user asks about current events, weather, news, prices, or anything that "
    "requires up-to-date information, you MUST use your web_search tool to find the "
    "answer — do NOT tell the user to look it up themselves. After searching, if you "
    "need more detail from a specific result, use fetch_webpage to read that page. "
    "Always provide a direct, helpful answer based on what you find. Refer to me by my name Roman if that makes the conversation flow more naturally.'"
    "If you don't know the answer, say you don't know — do not try to make up an answer."
)

PROMPTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompts.json")
CHATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved_chats.json")
APP_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_state.json")


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


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Claude Chatbot")
        self.root.geometry("710x620")

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
        self.pending_images = []  # list of (base64_data, media_type, filename)
        self.debug_enabled = tk.BooleanVar(value=True)
        self.tool_calls_enabled = tk.BooleanVar(value=True)
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        self.system_prompt_name = ""
        self.model = DEFAULT_MODEL
        self.prompt_editor_window = None
        self.available_models = self._fetch_available_models()

        self.setup_ui()
        self._load_last_state()
        self.root.after(50, self.check_queue)

    def setup_ui(self):
        # Grid weights for resizing
        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(1, weight=0)
        self.root.grid_rowconfigure(2, weight=1)
        self.root.grid_rowconfigure(3, weight=0)
        self.root.grid_rowconfigure(4, weight=0)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=0)

        # Model selection toolbar (row 0)
        model_toolbar = tk.Frame(self.root)
        model_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 0))

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

        # Chat management toolbar (row 1)
        chat_toolbar = tk.Frame(self.root)
        chat_toolbar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(5, 0))

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

        tk.Button(chat_toolbar, text="DELETE", command=self._delete_chat, width=8).pack(side=tk.LEFT, padx=(5, 5))
        tk.Button(chat_toolbar, text="NEW CHAT", command=self._new_chat, width=8).pack(side=tk.LEFT, padx=(5, 0))

        self._refresh_chat_list()

        # Chat display
        self.chat_display = tk.Text(
            self.root, wrap=tk.WORD, state="disabled", font=("Arial", 11)
        )
        self.chat_display.grid(row=2, column=0, sticky="nsew", padx=(10, 0), pady=10)

        # Scrollbar
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

        # Input field
        self.input_field = tk.Text(
            self.root, height=3, wrap=tk.WORD, font=("Arial", 11)
        )
        self.input_field.grid(
            row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 5)
        )
        self.input_field.bind("<Return>", self.on_enter_key)
        self.input_field.focus_set()

        # Button bar
        button_frame = tk.Frame(self.root)
        button_frame.grid(row=4, column=0, columnspan=2, pady=(0, 10))

        self.attach_button = tk.Button(
            button_frame, text="Attach Images", command=self.attach_image, width=14
        )
        self.attach_button.pack(side=tk.LEFT, padx=(0, 5))

        self.send_button = tk.Button(
            button_frame, text="Send", command=self.send_message, width=12
        )
        self.send_button.pack(side=tk.LEFT, padx=(5, 5))

        self.prompt_button = tk.Button(
            button_frame, text="System Prompt", command=self.open_prompt_editor, width=14
        )
        self.prompt_button.pack(side=tk.LEFT, padx=(5, 5))

        self.debug_toggle = tk.Checkbutton(
            button_frame, text="Debug", variable=self.debug_enabled,
            font=("Arial", 9),
        )
        self.debug_toggle.pack(side=tk.LEFT, padx=(5, 0))

        self.tool_calls_toggle = tk.Checkbutton(
            button_frame, text="Tool Calls", variable=self.tool_calls_enabled,
            font=("Arial", 9),
        )
        self.tool_calls_toggle.pack(side=tk.LEFT, padx=(5, 0))

        # Attachment indicator (hidden until an image is attached)
        self.attach_label = tk.Label(
            self.root, text="", foreground="#6a1b9a", font=("Arial", 9)
        )
        self.attach_label.grid(row=5, column=0, columnspan=2)

    # --- App State Persistence ---

    def _fetch_available_models(self):
        """Fetch available models from the Anthropic API, fall back to hardcoded list."""
        try:
            response = self.client.models.list(limit=100)
            # Build {id: display_name} mapping and id list
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
        # Map display name back to model ID
        selected_display = self._model_var.get()
        for mid in self._model_id_list:
            if self._model_display_names.get(mid, mid) == selected_display:
                self.model = mid
                break
        self._save_last_state()

    def _update_title(self):
        if self.system_prompt_name:
            self.root.title(f"Claude Chatbot — {self.system_prompt_name}")
        else:
            self.root.title("Claude Chatbot")

    def _load_last_state(self):
        """Restore the last-used system prompt on startup."""
        if not os.path.exists(APP_STATE_FILE):
            return
        try:
            with open(APP_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        prompt_name = state.get("last_system_prompt_name", "")
        if prompt_name:
            prompts = self._load_saved_prompts()
            if prompt_name in prompts:
                self.system_prompt = prompts[prompt_name]
                self.system_prompt_name = prompt_name
                self._update_title()
        model = state.get("last_model", "")
        if model and model in self.available_models:
            self.model = model
            self._model_var.set(self._model_display_names.get(model, model))

    def _save_last_state(self):
        """Persist the current system prompt name for next startup."""
        state = {
            "last_system_prompt_name": self.system_prompt_name,
            "last_model": self.model,
        }
        with open(APP_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    # --- System Prompt Editor ---

    def _load_saved_prompts(self):
        if os.path.exists(PROMPTS_FILE):
            with open(PROMPTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_prompts_to_disk(self, prompts):
        with open(PROMPTS_FILE, "w", encoding="utf-8") as f:
            json.dump(prompts, f, indent=2, ensure_ascii=False)

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
        self._prompt_text = tk.Text(win, wrap=tk.WORD, font=("Consolas", 10))
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
        prompts[name] = text
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
            self._prompt_text.delete("1.0", tk.END)
            self._prompt_text.insert("1.0", prompts[name])
            self._prompt_name_entry.delete(0, tk.END)
            self._prompt_name_entry.insert(0, name)

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

    # --- Chat Save / Load ---

    def _load_saved_chats(self):
        if os.path.exists(CHATS_FILE):
            with open(CHATS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_chats_to_disk(self, chats):
        with open(CHATS_FILE, "w", encoding="utf-8") as f:
            json.dump(chats, f, indent=2, ensure_ascii=False)

    def _refresh_chat_list(self):
        chats = self._load_saved_chats()
        self._chat_combo["values"] = list(chats.keys())

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
                cleaned["content"] = block["content"]
            return cleaned
        if btype == "image":
            return {"type": "text", "text": "[Image was attached]"}
        return block

    def _serialize_messages(self):
        """Convert messages to JSON-serializable format, stripping image data and extra fields."""
        serialized = []
        for msg in self.messages:
            content = msg["content"]
            if isinstance(content, str):
                serialized.append({"role": msg["role"], "content": content})
            elif isinstance(content, list):
                blocks = []
                for block in content:
                    if isinstance(block, dict):
                        blocks.append(self._clean_content_block(block))
                    elif hasattr(block, "model_dump"):
                        d = block.model_dump()
                        blocks.append(self._clean_content_block(d))
                    else:
                        blocks.append({"type": "text", "text": str(block)})
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
        chats = self._load_saved_chats()
        chats[name] = {
            "messages": self._serialize_messages(),
            "system_prompt": self.system_prompt,
            "system_prompt_name": self.system_prompt_name,
            "model": self.model,
        }
        self._save_chats_to_disk(chats)
        self._refresh_chat_list()
        self._chat_combo_var.set(name)

    def _load_chat(self):
        name = self._chat_combo_var.get()
        if not name:
            return
        chats = self._load_saved_chats()
        if name not in chats:
            messagebox.showwarning("Not found", f"No saved chat named '{name}'.")
            return
        chat_data = chats[name]
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
        chats = self._load_saved_chats()
        if name not in chats:
            messagebox.showwarning("Not found", f"No saved chat named '{name}'.")
            return
        chats.pop(name)
        self._save_chats_to_disk(chats)
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
                self.chat_display.insert(tk.END, "You: ", "user_label")
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
                    self.chat_display.insert(tk.END, "You: ", "user_label")
                    if has_images:
                        self.chat_display.insert(tk.END, "[Image] ", "image_info")
                    if text:
                        self.chat_display.insert(tk.END, text + "\n\n", "user")
                    else:
                        self.chat_display.insert(tk.END, "\n\n", "user")
            elif role == "assistant" and isinstance(content, str):
                self.chat_display.insert(tk.END, "Claude:\n", "assistant_label")
                self.chat_display.insert(tk.END, content + "\n\n", "assistant")
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
            return
        self.send_message()
        return "break"

    def send_message(self):
        if self.streaming:
            return

        user_text = self.input_field.get("1.0", "end-1c").strip()
        if not user_text and not self.pending_images:
            return

        # Clear input and disable send
        self.input_field.delete("1.0", tk.END)
        self.streaming = True
        self.send_button.config(state="disabled")

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

    def search_web(self, query):
        """Search the web using DuckDuckGo and return results."""
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
        """Fetch a URL and return extracted text content."""
        try:
            response = httpx.get(url, follow_redirects=True, timeout=15)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "html" in content_type:
                text = extract_text_from_html(response.text)
            else:
                text = response.text
            # Truncate to avoid blowing up context
            if len(text) > 20000:
                text = text[:20000] + "\n\n[Content truncated...]"
            return text
        except Exception as e:
            return f"Error fetching URL: {e}"

    def _check_powershell_safety(self, command):
        """Check command against safety tiers. Returns (allowed, message)."""
        for pattern in POWERSHELL_BLOCKED:
            if re.search(pattern, command, re.IGNORECASE):
                return "blocked", f"BLOCKED: Command matches dangerous pattern ({pattern})"

        for pattern in POWERSHELL_CONFIRM:
            if re.search(pattern, command, re.IGNORECASE):
                return "confirm", pattern
        return "safe", ""

    def _request_confirmation(self, command):
        """Request user confirmation from the main thread via queue. Returns True/False."""
        event = threading.Event()
        result_holder = [False]  # mutable container for the response

        def ask():
            answer = messagebox.askyesno(
                "PowerShell — Confirm Command",
                f"The following command requires your approval:\n\n{command}\n\nAllow execution?",
                default=messagebox.NO,
            )
            result_holder[0] = answer
            event.set()

        # Schedule the dialog on the main thread
        self.root.after(0, ask)
        event.wait()
        return result_holder[0]

    def run_powershell(self, command):
        """Execute a PowerShell command with safety checks."""
        # Tier 1 & 2 safety checks
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

    def _make_serializable(self, obj):
        """Convert SDK objects (ParsedTextBlock, ToolUseBlock, etc.) to plain dicts."""
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        return str(obj)

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

        # Truncate base64 image data for readability
        for msg in display_msgs:
            content = msg.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "image":
                        src = block.get("source", {})
                        if src.get("data"):
                            src["data"] = src["data"][:40] + "...[truncated]"

        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "stream": True,
            "system": self.system_prompt,
            "tools": TOOLS,
            "messages": display_msgs,
        }
        return json.dumps(payload, indent=2)

    def stream_worker(self, messages):
        try:
            # Insert the "Claude: " label before streaming begins
            self.queue.put({"type": "label"})

            call_num = 0
            while True:
                call_num += 1
                payload_text = self._payload_for_display(messages)
                self.queue.put({"type": "call_counter", "content": call_num})
                self.queue.put({"type": "debug", "content": payload_text})

                full_text = ""
                with self.client.messages.stream(
                    model=self.model,
                    max_tokens=4096,
                    system=self.system_prompt,
                    messages=messages,
                    tools=TOOLS,
                ) as stream:
                    for text in stream.text_stream:
                        full_text += text
                        self.queue.put({"type": "text_delta", "content": text})

                    final_message = stream.get_final_message()

                if final_message.stop_reason == "tool_use":
                    # Append the full assistant message (with tool_use blocks) to history
                    messages.append({"role": "assistant", "content": final_message.content})

                    # Process each tool call
                    tool_results = []
                    for block in final_message.content:
                        if block.type == "tool_use":
                            # Emit full tool call details for debug view
                            tool_call_detail = json.dumps(
                                {"tool": block.name, "id": block.id, "input": block.input},
                                indent=2,
                            )
                            self.queue.put({"type": "tool_call_debug", "content": tool_call_detail})

                            if block.name == "web_search":
                                query = block.input.get("query", "")
                                self.queue.put(
                                    {"type": "tool_info", "content": f"Searching: {query}\n"}
                                )
                                result = self.search_web(query)
                            elif block.name == "fetch_webpage":
                                url = block.input.get("url", "")
                                self.queue.put(
                                    {"type": "tool_info", "content": f"Fetching: {url}\n"}
                                )
                                result = self.fetch_url(url)
                            elif block.name == "run_powershell":
                                cmd = block.input.get("command", "")
                                self.queue.put(
                                    {"type": "tool_info", "content": f"Running: {cmd}\n"}
                                )
                                result = self.run_powershell(cmd)
                            else:
                                result = f"Unknown tool: {block.name}"

                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result,
                                }
                            )

                    messages.append({"role": "user", "content": tool_results})
                    # Continue the loop — Claude will stream its response using the tool results
                else:
                    # Normal end_turn — we're done
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
                    pass  # skip payload dump when disabled
                elif msg["type"] == "call_counter":
                    tag = "call_counter" if self.debug_enabled.get() else "call_counter_subtle"
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(
                        tk.END, f"  Call #{msg['content']}  ", tag
                    )
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
                    pass  # skip when tool calls display disabled
                elif msg["type"] == "tool_call_debug":
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, "--- TOOL CALL ---\n", "tool_debug_label")
                    self.chat_display.insert(tk.END, msg["content"] + "\n", "tool_debug")
                    self.chat_display.insert(tk.END, "--- END TOOL CALL ---\n", "tool_debug_label")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "label":
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, "Claude:\n", "assistant_label")
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "text_delta":
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, msg["content"], "assistant")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
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
                    self.send_button.config(state="normal")
                    self.input_field.focus_set()
                elif msg["type"] == "error":
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(
                        tk.END, f"Error: {msg['content']}\n\n", "error"
                    )
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                    self.streaming = False
                    self.send_button.config(state="normal")
        except queue.Empty:
            pass
        self.root.after(50, self.check_queue)

    def append_message(self, role, content, filenames=None):
        self.chat_display.config(state="normal")
        if role == "user":
            self.chat_display.insert(tk.END, "You: ", "user_label")
            if filenames:
                for name in filenames:
                    self.chat_display.insert(
                        tk.END, f"[Image: {name}] ", "image_info"
                    )
            self.chat_display.insert(tk.END, content + "\n\n", "user")
        self.chat_display.see(tk.END)
        self.chat_display.config(state="disabled")


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
