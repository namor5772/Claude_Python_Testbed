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
]

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant with access to web_search and fetch_webpage tools. "
    "When the user asks about current events, weather, news, prices, or anything that "
    "requires up-to-date information, you MUST use your web_search tool to find the "
    "answer — do NOT tell the user to look it up themselves. After searching, if you "
    "need more detail from a specific result, use fetch_webpage to read that page. "
    "Always provide a direct, helpful answer based on what you find. Refer to me by my name Roman if that makes the conversation flow more naturally. If you don't know the answer, say you don't know — do not try to make up an answer."
)

PROMPTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_prompts.json")


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
        self.root.geometry("700x600")

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
        self.system_prompt = DEFAULT_SYSTEM_PROMPT
        self.prompt_editor_window = None

        self.setup_ui()
        self.root.after(50, self.check_queue)

    def setup_ui(self):
        # Grid weights for resizing
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=0)
        self.root.grid_rowconfigure(2, weight=0)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=0)

        # Chat display
        self.chat_display = tk.Text(
            self.root, wrap=tk.WORD, state="disabled", font=("Arial", 11)
        )
        self.chat_display.grid(row=0, column=0, sticky="nsew", padx=(10, 0), pady=10)

        # Scrollbar
        scrollbar = tk.Scrollbar(self.root, command=self.chat_display.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", pady=10, padx=(0, 10))
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
            row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 5)
        )
        self.input_field.bind("<Return>", self.on_enter_key)
        self.input_field.focus_set()

        # Button bar
        button_frame = tk.Frame(self.root)
        button_frame.grid(row=2, column=0, columnspan=2, pady=(0, 10))

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

        # Attachment indicator (hidden until an image is attached)
        self.attach_label = tk.Label(
            self.root, text="", foreground="#6a1b9a", font=("Arial", 9)
        )
        self.attach_label.grid(row=3, column=0, columnspan=2)

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
        self.prompt_editor_window.destroy()

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
            "model": "claude-sonnet-4-5-20250929",
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
                    model="claude-sonnet-4-5-20250929",
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
