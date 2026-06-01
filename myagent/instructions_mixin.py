import os, json, copy, base64, tkinter as tk
from tkinter import messagebox, filedialog, ttk

from myagent.constants import (
    IS_WINDOWS, INSTRUCTIONS_FILE, DEFAULT_INSTRUCTION, DEFAULT_GEOMETRY,
    PROVIDERS, FALLBACK_MODELS, OPENAI_FALLBACK_MODELS, GEMINI_FALLBACK_MODELS,
    OLLAMA_FALLBACK_MODELS, EFFORT_LEVELS, ADAPTIVE_MODE_VALUES,
    ADAPTIVE_MODE_VALUES_NO_MAX, BUDGET_PRESETS, MONO_FONT, _HAS_DESKTOP, _HAS_MCP,
    _HAS_GOOGLE, _HAS_PROTONMAIL, _HAS_OUTLOOK, DEFAULT_MODEL, OPENAI_DEFAULT_MODEL,
    GEMINI_DEFAULT_MODEL, OLLAMA_DEFAULT_MODEL,
)


class InstructionsMixin:

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
                mcp = "mcp" if entry.get("mcp") else ""
                google = "google" if entry.get("google") else ""
                outlook = "outlook" if entry.get("outlook") else ""
                convo = "convo" if entry.get("conversational") else ""
                flags = " ".join(f for f in [desktop, browser, meta, mcp, google, outlook, convo] if f)
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
                "mcp": entry.get("mcp", False),
                "google": entry.get("google", False),
                "outlook": entry.get("outlook", False),
                "conversational": entry.get("conversational", False),
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
                "mcp": params.get("mcp", False),
                "google": params.get("google", False),
                "outlook": params.get("outlook", False),
                "conversational": params.get("conversational", False),
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
            updatable = ("text", "desktop", "browser", "meta", "mcp", "google",
                         "outlook", "conversational", "skill_modes", "provider", "model",
                         "temperature", "thinking_enabled", "thinking_effort",
                         "thinking_budget", "thinking_mode", "text_verbosity")
            if all(params.get(k) is None for k in updatable):
                return (
                    "Error: At least one of 'text', 'desktop', 'browser', 'meta', "
                    "'skill_modes', 'provider', 'model', 'temperature', "
                    "'thinking_enabled', 'thinking_effort', 'thinking_budget', "
                    "'thinking_mode', or 'text_verbosity' must be provided for update."
                )
            entry = instructions[name]
            for key in ("text", "desktop", "browser", "meta", "mcp", "google",
                        "outlook", "conversational", "provider", "model", "temperature",
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
            if name == self.agent_instruction_name:
                return f"Instruction '{name}' updated on disk. Changes will take effect next time it is loaded."
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
        win.title("Instruction Editor")
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

        tk.Button(win, text="SAVE", command=self._save_instruction, width=6).grid(
            row=0, column=2, padx=5, pady=(10, 5)
        )
        tk.Button(win, text="DELETE", command=self._delete_instruction, width=6).grid(
            row=0, column=3, padx=5, pady=(10, 5)
        )
        tk.Button(win, text="CLEAR", command=self._clear_instruction_editor, width=6).grid(
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

        _apply_btn = tk.Button(
            win, text="Apply", command=self._apply_instruction, width=6
        )
        import tkinter.font as _tkfont
        _f = _tkfont.nametofont(_apply_btn.cget("font")).copy()
        _f.configure(weight="bold")
        _apply_btn.configure(font=_f)
        _apply_btn.grid(row=1, column=2, padx=5, pady=5)

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
                               or (p == "Gemini" and self._has_gemini)
                               or (p == "Ollama" and self._has_ollama)]
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

        # Row 4: Tool toggle checkboxes
        checks_frame = tk.Frame(win)
        checks_frame.grid(row=4, column=0, columnspan=6, sticky="ew", padx=10, pady=(5, 0))

        self._editor_desktop = tk.BooleanVar(value=self.desktop_enabled.get() if _HAS_DESKTOP else False)
        self._editor_browser = tk.BooleanVar(value=self.browser_enabled.get())
        self._editor_meta = tk.BooleanVar(value=self.meta_enabled.get())
        self._editor_mcp = tk.BooleanVar(value=self.mcp_enabled.get() if _HAS_MCP else False)
        self._editor_google = tk.BooleanVar(value=self.google_enabled.get() if _HAS_GOOGLE else False)
        self._editor_proton = tk.BooleanVar(value=self.proton_enabled.get() if _HAS_PROTONMAIL else False)
        self._editor_outlook = tk.BooleanVar(value=self.outlook_enabled.get() if _HAS_OUTLOOK else False)
        self._editor_conversational = tk.BooleanVar(value=self.conversational_enabled.get())
        _desktop_cb = tk.Checkbutton(
            checks_frame, text="Desktop", variable=self._editor_desktop,
            font=("Arial", 9),
        )
        _desktop_cb.pack(side=tk.LEFT, padx=(0, 5))
        if not _HAS_DESKTOP:
            _desktop_cb.config(state=tk.DISABLED)
        tk.Checkbutton(
            checks_frame, text="Browser", variable=self._editor_browser,
            font=("Arial", 9),
        ).pack(side=tk.LEFT, padx=(5, 0))
        tk.Checkbutton(
            checks_frame, text="Meta", variable=self._editor_meta,
            font=("Arial", 9),
        ).pack(side=tk.LEFT, padx=(5, 0))
        _mcp_cb = tk.Checkbutton(
            checks_frame, text="MCP", variable=self._editor_mcp,
            font=("Arial", 9),
        )
        _mcp_cb.pack(side=tk.LEFT, padx=(5, 0))
        if not _HAS_MCP:
            _mcp_cb.config(state=tk.DISABLED)
        _google_cb = tk.Checkbutton(
            checks_frame, text="Google", variable=self._editor_google,
            font=("Arial", 9),
        )
        _google_cb.pack(side=tk.LEFT, padx=(5, 0))
        if not _HAS_GOOGLE:
            _google_cb.config(state=tk.DISABLED)
        _proton_cb = tk.Checkbutton(
            checks_frame, text="IMAP", variable=self._editor_proton,
            font=("Arial", 9),
        )
        _proton_cb.pack(side=tk.LEFT, padx=(5, 0))
        if not _HAS_PROTONMAIL:
            _proton_cb.config(state=tk.DISABLED)
        _outlook_cb = tk.Checkbutton(
            checks_frame, text="Outlook", variable=self._editor_outlook,
            font=("Arial", 9),
        )
        _outlook_cb.pack(side=tk.LEFT, padx=(5, 0))
        if not _HAS_OUTLOOK:
            _outlook_cb.config(state=tk.DISABLED)
        tk.Checkbutton(
            checks_frame, text="Convo", variable=self._editor_conversational,
            font=("Arial", 9),
        ).pack(side=tk.LEFT, padx=(5, 0))

        # Row 5: Skills + Shell/PS Safety buttons
        buttons_frame = tk.Frame(win)
        buttons_frame.grid(row=5, column=0, columnspan=6, sticky="ew", padx=10, pady=(5, 0))

        self.skills_button = tk.Button(
            buttons_frame, text="Skills", command=self.open_skills_editor, padx=10
        )
        self.skills_button.pack(side=tk.LEFT, padx=(0, 5))
        self._update_skills_button()

        self.ps_safety_button = tk.Button(
            buttons_frame, text="Safety", command=self._open_ps_safety_dialog, padx=10
        )
        self.ps_safety_button.pack(side=tk.LEFT, padx=(5, 0))
        self._update_ps_safety_button()

        # Row 6: Image management buttons
        img_frame = tk.Frame(win)
        img_frame.grid(row=6, column=0, columnspan=6, sticky="ew", padx=10, pady=(5, 0))

        tk.Button(
            img_frame, text="Attach Images", command=self.attach_image, width=15
        ).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(
            img_frame, text="Remove Selected", command=self._remove_selected_images, width=15
        ).pack(side=tk.LEFT, padx=(0, 5))

        # Row 7: Image listbox
        self._instr_image_listbox = tk.Listbox(
            win, height=4, font=("Arial", 9), foreground="#6a1b9a",
            selectmode=tk.EXTENDED,
        )
        self._instr_image_listbox.grid(
            row=7, column=0, columnspan=5, sticky="ew", padx=10, pady=(3, 5)
        )
        img_list_scrollbar = tk.Scrollbar(win, command=self._instr_image_listbox.yview)
        img_list_scrollbar.grid(row=7, column=5, sticky="ns", pady=(3, 5), padx=(0, 5))
        self._instr_image_listbox.config(yscrollcommand=img_list_scrollbar.set)

        # (Apply button is in row 1, to the right of the Load combo)

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
        self.mcp_enabled.set(self._editor_mcp.get())
        self.google_enabled.set(self._editor_google.get())
        self.proton_enabled.set(self._editor_proton.get())
        self.outlook_enabled.set(self._editor_outlook.get())
        self.conversational_enabled.set(self._editor_conversational.get())
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
            "mcp": self.mcp_enabled.get(),
            "google": self.google_enabled.get(),
            "proton": self.proton_enabled.get(),
            "outlook": self.outlook_enabled.get(),
            "conversational": self.conversational_enabled.get(),
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
        self._editor_mcp.set(False)
        self._editor_google.set(False)
        self._editor_proton.set(False)
        self._editor_conversational.set(False)
        self._disabled_confirm_patterns = set()
        self._update_ps_safety_button()
        # Reset model controls to defaults
        if self._has_anthropic:
            default_provider = "Anthropic"
            default_model = DEFAULT_MODEL
        elif self._has_openai:
            default_provider = "OpenAI"
            default_model = OPENAI_DEFAULT_MODEL
        elif self._has_gemini:
            default_provider = "Gemini"
            default_model = GEMINI_DEFAULT_MODEL
        else:
            default_provider = "Ollama"
            default_model = OLLAMA_DEFAULT_MODEL
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
            self._editor_mcp.set(entry.get("mcp", False))
            self._editor_google.set(entry.get("google", False))
            self._editor_proton.set(entry.get("proton", False))
            self._editor_outlook.set(entry.get("outlook", False))
            self._editor_conversational.set(entry.get("conversational", False))
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
        self.mcp_enabled.set(self._editor_mcp.get())
        self.google_enabled.set(self._editor_google.get())
        self.proton_enabled.set(self._editor_proton.get())
        self.outlook_enabled.set(self._editor_outlook.get())
        self.conversational_enabled.set(self._editor_conversational.get())
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
