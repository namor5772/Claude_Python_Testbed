import os, re, json, io, base64, tkinter as tk
from tkinter import filedialog, messagebox

from myagent.constants import CHATS_DIR

try:
    from PIL import Image
except ImportError:
    Image = None


class ChatMixin:

    MAX_IMAGE_BYTES = 4_800_000  # stay under Anthropic's 5MB limit

    @staticmethod
    def _sanitize_filename(name, ext='.json'):
        safe = re.sub(r'[<>:"/\\|?*]', '_', name)
        safe = safe.strip('. ')
        return (safe or '_') + ext

    @staticmethod
    def _chat_file_path(name):
        return os.path.join(CHATS_DIR, ChatMixin._sanitize_filename(name))

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
                            sub_blocks.append(ChatMixin._clean_content_block(sub))
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

    # ── LaTeX Processing ───────────────────────────────────────────────

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
        return re.sub(r'\{([^}]*)\}', r'\1', text)
