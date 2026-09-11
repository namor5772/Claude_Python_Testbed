import os, json, tkinter as tk
from tkinter import font as tkfont, messagebox, ttk

from myagent.constants import (
    IS_WINDOWS, INSTRUCTIONS_FILE, DEFAULT_INSTRUCTION, PROVIDERS,
    ADAPTIVE_MODE_VALUES, MONO_FONT, _HAS_DESKTOP, _HAS_MCP,
    _HAS_GOOGLE, _HAS_PROTONMAIL, _HAS_OUTLOOK, _HAS_EXCEL,
    DEFAULT_MODEL, OPENAI_DEFAULT_MODEL,
    GEMINI_DEFAULT_MODEL, XAI_DEFAULT_MODEL, KIMI_DEFAULT_MODEL, OLLAMA_DEFAULT_MODEL,
)
from myagent.datapaths import absorb_conflict_forks, load_store, save_store
from myagent.instruction_layout import (
    UNFILED, UNFILED_LABEL, drop, file_page, layout, move_page, move_section,
    normalize_section, rename_section, renumber, rows, section_of, sections,
)
from myagent.keyboard import bind_mnemonics


class InstructionsMixin:

    # ── Agent Instruction Editor ────────────────────────────────────────

    def _load_saved_instructions(self):
        """Load instructions from disk. Each entry is {text: str, images: list}.
        Migrates old string-only entries automatically. load_store also runs
        the one-shot repo-root→OneDrive migration, so it must be called before
        any existence check; a file that exists but doesn't parse (possibly a
        half-synced cloud write) is served as a session-only Default WITHOUT
        overwriting the store — the next launch retries."""
        data = load_store(INSTRUCTIONS_FILE)
        if data:
            changed = absorb_conflict_forks(INSTRUCTIONS_FILE, data)
            # Migrate old format: {name: "text"} → {name: {text: "...", images: []}}
            for name, entry in list(data.items()):
                if isinstance(entry, str):
                    data[name] = {"text": entry, "images": []}
                    changed = True
                elif isinstance(entry, dict) and "images" not in entry:
                    entry["images"] = []
                    changed = True
            if changed:
                self._save_instructions_to_disk(data)
            return data
        instructions = {"Default": {"text": DEFAULT_INSTRUCTION, "images": []}}
        if not os.path.exists(INSTRUCTIONS_FILE):
            self._save_instructions_to_disk(instructions)
        return instructions

    def _save_instructions_to_disk(self, instructions):
        save_store(INSTRUCTIONS_FILE, instructions)

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
                excel = "excel" if entry.get("excel") else ""
                meta = "meta" if entry.get("meta") else ""
                mcp = "mcp" if entry.get("mcp") else ""
                google = "google" if entry.get("google") else ""
                outlook = "outlook" if entry.get("outlook") else ""
                convo = "convo" if entry.get("conversational") else ""
                flags = " ".join(f for f in [desktop, browser, excel, meta, mcp, google, outlook, convo] if f)
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
                "excel": entry.get("excel", False),
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
                "blocked_tools": entry.get("blocked_tools", []),
            }
            return json.dumps(info, indent=2)

        if action == "create":
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
                "excel": params.get("excel", False),
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
                "blocked_tools": sorted(params["blocked_tools"])
                                 if params.get("blocked_tools") is not None
                                 else sorted(getattr(self, "_blocked_tools", [])),
            }
            instructions[name] = entry
            self._save_instructions_to_disk(instructions)
            return f"Instruction '{name}' created successfully."

        if action == "update":
            if name not in instructions:
                return f"Error: Instruction '{name}' not found. Use 'create' to add it."
            updatable = ("text", "desktop", "browser", "excel", "meta", "mcp", "google",
                         "outlook", "conversational", "skill_modes", "provider", "model",
                         "temperature", "thinking_enabled", "thinking_effort",
                         "thinking_budget", "thinking_mode", "text_verbosity",
                         "blocked_tools")
            if all(params.get(k) is None for k in updatable):
                return (
                    "Error: At least one of 'text', 'desktop', 'browser', 'meta', "
                    "'skill_modes', 'provider', 'model', 'temperature', "
                    "'thinking_enabled', 'thinking_effort', 'thinking_budget', "
                    "'thinking_mode', or 'text_verbosity' must be provided for update."
                )
            entry = instructions[name]
            for key in ("text", "desktop", "browser", "excel", "meta", "mcp", "google",
                        "outlook", "conversational", "provider", "model", "temperature",
                        "thinking_enabled", "thinking_effort",
                        "thinking_budget", "thinking_mode", "text_verbosity",
                        "blocked_tools"):
                val = params.get(key)
                if val is not None:
                    entry[key] = sorted(val) if key == "blocked_tools" else val
            skill_modes = params.get("skill_modes")
            if skill_modes is not None:
                existing = entry.get("skill_modes", {})
                existing.update(skill_modes)
                entry["skill_modes"] = existing
            self._save_instructions_to_disk(instructions)
            if name == self.agent_instruction_name:
                return f"Instruction '{name}' updated on disk. Changes will take effect next time it is loaded."
            return f"Instruction '{name}' updated successfully."

        if action == "delete":
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

        save_btn = tk.Button(win, text="SAVE", command=self._save_instruction, width=6)
        save_btn.grid(row=0, column=2, padx=5, pady=(10, 5))
        delete_btn = tk.Button(win, text="DELETE", command=self._delete_instruction, width=6)
        delete_btn.grid(row=0, column=3, padx=5, pady=(10, 5))
        clear_btn = tk.Button(win, text="CLEAR", command=self._clear_instruction_editor, width=6)
        clear_btn.grid(row=0, column=4, padx=5, pady=(10, 5))
        # Apply ends the row (since 2026-09-11 — the Load Instruction row it
        # used to share with the old combobox became the list beside the text)
        _apply_btn = tk.Button(
            win, text="Apply", command=self._apply_instruction, width=6
        )
        _apply_btn.grid(row=0, column=5, padx=(15, 10), pady=(10, 5))

        # Row 1: Model / provider controls
        model_frame = tk.Frame(win)
        model_frame.grid(row=1, column=0, columnspan=6, sticky="ew", padx=10, pady=(5, 0))

        available_providers = [p for p in PROVIDERS
                               if (p == "Anthropic" and self._has_anthropic)
                               or (p == "OpenAI" and self._has_openai)
                               or (p == "Google" and self._has_gemini)
                               or (p == "xAI" and self._has_xai)
                               or (p == "Moonshot" and self._has_kimi)
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

        # Row 2: Tool toggle checkboxes
        checks_frame = tk.Frame(win)
        checks_frame.grid(row=2, column=0, columnspan=6, sticky="ew", padx=10, pady=(5, 0))

        self._editor_desktop = tk.BooleanVar(value=self.desktop_enabled.get() if _HAS_DESKTOP else False)
        self._editor_browser = tk.BooleanVar(value=self.browser_enabled.get())
        self._editor_excel = tk.BooleanVar(value=self.excel_enabled.get() if _HAS_EXCEL else False)
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
        _excel_cb = tk.Checkbutton(
            checks_frame, text="Excel", variable=self._editor_excel,
            font=("Arial", 9),
        )
        _excel_cb.pack(side=tk.LEFT, padx=(5, 0))
        if not _HAS_EXCEL:
            _excel_cb.config(state=tk.DISABLED)
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
            checks_frame, text="Gmail", variable=self._editor_google,
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

        # Row 3: Skills + Shell/PS Safety buttons
        buttons_frame = tk.Frame(win)
        buttons_frame.grid(row=3, column=0, columnspan=6, sticky="ew", padx=10, pady=(5, 0))

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

        # Row 4: Image management — same layout as the Agent Request dialog:
        # buttons stacked on the left, listbox filling the width to the right
        img_frame = tk.Frame(win)
        img_frame.grid(row=4, column=0, columnspan=6, sticky="ew", padx=10, pady=(5, 5))
        img_frame.grid_columnconfigure(2, weight=1)

        attach_btn = tk.Button(
            img_frame, text="Attach Images", command=self.attach_image, width=15
        )
        attach_btn.grid(row=0, column=0, padx=(0, 5), sticky="nw")
        remove_btn = tk.Button(
            img_frame, text="Remove Selected", command=self._remove_selected_images, width=15
        )
        remove_btn.grid(row=1, column=0, padx=(0, 5), pady=(5, 0), sticky="nw")

        self._instr_image_listbox = tk.Listbox(
            img_frame, height=3, font=("Arial", 9), foreground="#6a1b9a",
            selectmode=tk.EXTENDED, exportselection=False,
        )
        self._instr_image_listbox.grid(row=0, column=2, rowspan=2, sticky="ew")
        img_list_scrollbar = tk.Scrollbar(img_frame, command=self._instr_image_listbox.yview)
        img_list_scrollbar.grid(row=0, column=3, rowspan=2, sticky="ns")
        self._instr_image_listbox.config(yscrollcommand=img_list_scrollbar.set)

        # Row 5: the bottom row (since 2026-09-10) and the only weighted one,
        # so resizing the window grows it alone. Since 2026-09-11 it is a
        # two-pane split — the Instructions list on the left (sections
        # and their pages, OneNote-style; the model is instruction_layout.py),
        # the instruction text on the right — with a draggable sash whose
        # position is remembered per instance (agent_state.json). The list is
        # built first and the text last, so Tab traversal runs list → ▲ → ▼ →
        # Section… → text, and the text stays the editor's last stop.
        paned = tk.PanedWindow(win, orient=tk.HORIZONTAL, sashwidth=5,
                               sashrelief="raised", bd=0)
        paned.grid(row=5, column=0, columnspan=6, sticky="nsew", padx=10, pady=(5, 10))
        self._instr_list_pane = list_pane = tk.Frame(paned)
        text_pane = tk.Frame(paned)
        paned.add(list_pane, width=getattr(self, "_instr_list_width", None) or 230,
                  minsize=150)
        paned.add(text_pane, minsize=200)   # the stretchy pane (Tk stretches the last)
        self._build_instruction_list(list_pane)

        text_pane.grid_rowconfigure(0, weight=1)
        text_pane.grid_columnconfigure(0, weight=1)
        self._instr_text = tk.Text(text_pane, wrap=tk.WORD, font=(MONO_FONT, 10))
        self._instr_text.grid(row=0, column=0, sticky="nsew")
        instr_scrollbar = tk.Scrollbar(text_pane, command=self._instr_text.yview)
        instr_scrollbar.grid(row=0, column=1, sticky="ns")
        self._instr_text.config(yscrollcommand=instr_scrollbar.set)
        # Ctrl+V with an image on the clipboard attaches it (text pastes normally)
        self._instr_text.bind("<Control-v>", self._on_editor_paste_image)
        self._instr_text.bind("<Control-V>", self._on_editor_paste_image)
        if not IS_WINDOWS:
            self._instr_text.bind("<Command-v>", self._on_editor_paste_image)

        # Grid weights
        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(5, weight=1)

        # Keyboard operation (myagent/keyboard.py): Alt+letter for every
        # button and field (the letters are tabled in the README — nothing is
        # underlined), Ctrl+S = SAVE, Ctrl+Enter = Apply (also from inside
        # the text, where plain Enter is a newline). Escape / Ctrl+Tab leave
        # the text area (class binding); the editor never closes on Escape,
        # since closing discards the draft. The list's own keys (Enter,
        # Alt+Up / Alt+Down) are bound in _build_instruction_list.
        bind_mnemonics(win, {
            "s": save_btn, "d": delete_btn, "c": clear_btn, "a": _apply_btn,
            "n": self._instr_name_entry,
            "l": self._instr_tree,
            "t": self._instr_section_btn,
            "m": self._model_combo,
            "p": self._provider_combo,
            "k": self.skills_button, "f": self.ps_safety_button,
            "i": attach_btn, "r": remove_btn,
            "e": self._instr_text,
        })

        def _save_key(event):
            self._save_instruction()
            return "break"

        def _apply_key(event):
            self._apply_instruction()
            return "break"

        win.bind("<Control-s>", _save_key)
        for widget in (win, self._instr_text):
            widget.bind("<Control-Return>", _apply_key)
            widget.bind("<Control-KP_Enter>", _apply_key)

        # Work on a copy of images so closing without Apply discards changes
        self._editor_images = list(self.pending_images)

        # Load current instruction into editor: the LIVE text (it can differ
        # from the saved one after an Apply), with its row selected in the
        # list — a selection the list must not answer by reloading the disk
        # copy, which is what _instr_shown_name guards (see
        # _on_instruction_selected).
        self._instr_text.insert("1.0", self.agent_instruction)
        self._instr_shown_name = self.agent_instruction_name
        if self.agent_instruction_name:
            self._instr_name_entry.insert(0, self.agent_instruction_name)
        self._refresh_instruction_list(select=("page", self.agent_instruction_name))
        self._refresh_image_listbox()

        # Restore geometry AFTER all content is laid out, then show
        win.update_idletasks()
        self._place_window(win, "editor", (900, 640), min_size=(400, 300))
        win.deiconify()
        # The list could not scroll to its selection before it had a size
        win.update_idletasks()
        self._select_instruction_row(self._selected_instruction_row())
        # Initial focus: the text area, cursor at the end of the instruction
        self._instr_text.mark_set("insert", "end-1c")
        self._instr_text.focus_set()

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
        self._instr_tree = None
        self._instr_list_pane = None

    def _capture_editor_geometry(self):
        """Cache the editor window's geometry for restore on next open."""
        self._remember_geometry("editor", self.instruction_editor_window)

    def _close_editor(self):
        """Capture geometry (and the Instructions pane's width, i.e. the
        sash position), destroy the editor, and nullify widget refs."""
        self._capture_editor_geometry()
        try:
            self._instr_list_width = self._instr_list_pane.winfo_width()
        except (AttributeError, tk.TclError):
            pass
        # Capture PS Safety dialog geometry before editor destroy cascades to it
        ps_dlg = getattr(self, '_ps_safety_dialog', None)
        if ps_dlg is not None:
            self._remember_geometry("ps_safety", ps_dlg)
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

    # ── Instructions list: sections and pages (2026-09-11) ──────────────
    #
    # The list replaced the Load Instruction combobox (whose "arbitrary" order
    # was the store's dict order). It is a ttk.Treeview: one header row per
    # section with the section's pages beneath, unfiled pages last under an
    # "Unfiled" header. Row ids are "s:<section>" and "p:<name>". The order
    # and sections live on the store entries (instruction_layout.py, pure and
    # unit-tested); everything here is widgets and gestures.

    def _build_instruction_list(self, pane):
        """Build the list inside `pane`: the tree, its scrollbar, and the
        ▲ / ▼ / Section… buttons under it. Gestures: selecting a page loads
        it into the editor (as choosing it in the old combobox did), Enter on
        a page jumps to the text, Alt+Up / Alt+Down move the selected page
        (or section) a step, a row can be dragged onto another row, and each
        section's open / closed state is remembered in agent_state.json."""
        pane.grid_rowconfigure(0, weight=1)
        pane.grid_columnconfigure(0, weight=1)
        style = ttk.Style(pane)
        row_font = tkfont.Font(root=pane, font=("Arial", 10))
        # A shallow indent (Tk's default is 20 px) leaves long names more room
        style.configure("Instr.Treeview", font=("Arial", 10), indent=10,
                        rowheight=row_font.metrics("linespace") + 6)
        style.configure("Instr.Treeview.Heading", font=("Arial", 10))
        tree = ttk.Treeview(pane, show="tree headings", selectmode="browse",
                            style="Instr.Treeview")
        # "Instructions" since 2026-09-12: the first day's heading, "Load
        # Instruction" (the old combobox's label carried over), read like a
        # button to press — when selecting a page IS the load.
        tree.heading("#0", text="Instructions", anchor="w")
        tree.column("#0", stretch=True, minwidth=80)
        tree.grid(row=0, column=0, sticky="nsew")
        list_scrollbar = tk.Scrollbar(pane, command=tree.yview)
        list_scrollbar.grid(row=0, column=1, sticky="ns")
        tree.config(yscrollcommand=list_scrollbar.set)
        # Section headers are bold on a grey band, the Unfiled header italic
        # on the same band, and pages alternate white / off-white — the row
        # separation a Tk 8.6 Treeview has no rule lines for.
        tree.tag_configure("section", font=("Arial", 10, "bold"), background="#e4e4e4")
        tree.tag_configure("unfiled", font=("Arial", 10, "italic"),
                           foreground="#555555", background="#e4e4e4")
        tree.tag_configure("odd", background="#f3f3f3")
        self._instr_tree = tree

        btn_row = tk.Frame(pane)
        btn_row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))
        up_btn = tk.Button(btn_row, text="▲", width=3,
                           command=lambda: self._move_selected_instruction(-1))
        up_btn.pack(side=tk.LEFT)
        down_btn = tk.Button(btn_row, text="▼", width=3,
                             command=lambda: self._move_selected_instruction(1))
        down_btn.pack(side=tk.LEFT, padx=(3, 0))
        self._instr_section_btn = tk.Button(
            btn_row, text="Section…", command=self._open_section_dialog, padx=6)
        self._instr_section_btn.pack(side=tk.LEFT, padx=(8, 0))

        tree.bind("<<TreeviewSelect>>", self._on_instruction_selected)
        tree.bind("<<TreeviewOpen>>", lambda e: self._on_section_toggled(opened=True))
        tree.bind("<<TreeviewClose>>", lambda e: self._on_section_toggled(opened=False))
        for seq in ("<Return>", "<KP_Enter>"):
            tree.bind(seq, self._on_tree_return)
        # Widget-level bindings run before the Treeview class ones and
        # break, so Alt+Up does not ALSO step the selection like a plain Up.
        tree.bind("<Alt-Up>", lambda e: (self._move_selected_instruction(-1), "break")[1])
        tree.bind("<Alt-Down>", lambda e: (self._move_selected_instruction(1), "break")[1])
        tree.bind("<ButtonPress-1>", self._on_tree_press)
        tree.bind("<B1-Motion>", self._on_tree_motion)
        tree.bind("<ButtonRelease-1>", self._on_tree_release)
        self._instr_drag = None

    @staticmethod
    def _populate_instruction_tree(tree, instructions, collapsed=()):
        """Rebuild the list from the store: a header per section (id
        "s:<section>", the unfiled pseudo-section's is "s:"), its pages
        beneath (id "p:<name>"), in instruction_layout order; sections named
        in `collapsed` start closed."""
        tree.delete(*tree.get_children(""))
        for section, names in layout(instructions):
            header = tree.insert(
                "", "end", iid=f"s:{section}", text=section or UNFILED_LABEL,
                open=(section not in collapsed),
                tags=("section",) if section else ("unfiled",))
            for k, name in enumerate(names):
                tree.insert(header, "end", iid=f"p:{name}", text=name,
                            tags=("odd",) if k % 2 else ())

    @staticmethod
    def _row_of(iid):
        """("page", name) or ("section", section) for a list row id."""
        return ("page", iid[2:]) if iid.startswith("p:") else ("section", iid[2:])

    def _collapsed(self):
        """The set of section names the user has collapsed (persisted)."""
        collapsed = getattr(self, "_collapsed_sections", None)
        if collapsed is None:
            collapsed = self._collapsed_sections = set()
        return collapsed

    def _refresh_instruction_list(self, instructions=None, select=None):
        """Rebuild the list (from `instructions`, else the store) and select
        `select` — a ("page", name) / ("section", section) row — or, when
        None, whatever was selected before if it still exists."""
        tree = getattr(self, "_instr_tree", None)
        try:
            if tree is None or not tree.winfo_exists():
                return
        except tk.TclError:
            return
        if instructions is None:
            instructions = self._load_saved_instructions()
        if select is None:
            select = self._selected_instruction_row()
        self._populate_instruction_tree(tree, instructions, self._collapsed())
        self._select_instruction_row(select)

    def _selected_instruction_row(self):
        """The selected list row as ("page", name) / ("section", section), or None."""
        try:
            sel = self._instr_tree.selection()
        except (AttributeError, tk.TclError):
            return None
        return self._row_of(sel[0]) if sel else None

    def _selected_instruction_name(self):
        """The selected page's name, or "" (nothing, or a section header, selected)."""
        row = self._selected_instruction_row()
        return row[1] if row and row[0] == "page" else ""

    def _select_instruction_row(self, row):
        """Select the list row `row` (("page", name) / ("section", section)),
        scrolled into view — opening its section if that was collapsed — or
        clear the selection when `row` is None or names no row. Selection
        events this queues are ignored by _on_instruction_selected while the
        page is the one already shown."""
        tree = self._instr_tree
        iid = None
        if row:
            kind, key = row
            iid = f"{'p' if kind == 'page' else 's'}:{key}"
            if not tree.exists(iid):
                iid = None
        if iid is None:
            if tree.selection():
                tree.selection_set(())
            return
        parent = tree.parent(iid)
        if parent:
            self._collapsed().discard(parent[2:])   # `see` opens it — keep the set true
        tree.selection_set(iid)
        tree.focus(iid)
        tree.see(iid)

    def _on_tree_return(self, event=None):
        """Enter on a page: it is already loaded (selection loads), so jump
        to the text with the cursor at its end. On a section header Tk's own
        binding toggles it open / closed."""
        if not self._selected_instruction_name():
            return None
        self._instr_text.mark_set("insert", "end-1c")
        self._instr_text.focus_set()
        return "break"

    def _on_section_toggled(self, opened):
        """<<TreeviewOpen>> / <<TreeviewClose>>: remember the user's choice.
        Tk sets the focus item to the toggled row before generating them."""
        try:
            iid = self._instr_tree.focus()
        except (AttributeError, tk.TclError):
            return
        if iid.startswith("s:"):
            (self._collapsed().discard if opened else self._collapsed().add)(iid[2:])

    def _move_selected_instruction(self, delta):
        """▲ / ▼, Alt+Up / Alt+Down: move the selected page (or section) one
        step — a page steps into the neighbouring section at its section's
        edge — and save the new order to the shared store."""
        row = self._selected_instruction_row()
        if row is None:
            return
        kind, key = row
        instructions = self._load_saved_instructions()
        if kind == "page":
            moved = move_page(instructions, key, delta)
        else:
            moved = move_section(instructions, key, delta)
        if moved:
            self._save_instructions_to_disk(instructions)
            self._refresh_instruction_list(instructions, select=row)

    def _section_for_new_page(self, instructions):
        """Where a newly SAVEd name is filed: the section of the selected
        page (or the selected section itself), else unfiled."""
        row = self._selected_instruction_row()
        if row is None:
            return UNFILED
        kind, key = row
        return key if kind == "section" else section_of(instructions.get(key, {}))

    # Drag-and-drop within the list: press on a row, release on another.
    # The press still selects (the class binding runs after this one), so a
    # dragged page is loaded like a clicked one; a release on the row that
    # was pressed is a plain click, so a click never reorders anything.

    def _on_tree_press(self, event):
        tree = self._instr_tree
        iid = tree.identify_row(event.y)
        region = tree.identify_region(event.x, event.y)
        self._instr_drag = iid if iid and region in ("tree", "cell") else None

    def _on_tree_motion(self, event):
        if self._instr_drag:
            tree = self._instr_tree
            over = tree.identify_row(event.y)
            tree.config(cursor="hand2" if over and over != self._instr_drag else "")

    def _on_tree_release(self, event):
        tree = self._instr_tree
        source, self._instr_drag = self._instr_drag, None
        tree.config(cursor="")
        target = tree.identify_row(event.y) if source else ""
        if not target or target == source:
            return
        kind, key = self._row_of(source)
        target_kind, target_key = self._row_of(target)
        instructions = self._load_saved_instructions()
        if drop(instructions, kind, key, target_kind, target_key):
            self._save_instructions_to_disk(instructions)
            self._refresh_instruction_list(instructions, select=(kind, key))

    def _open_section_dialog(self):
        """Section…: file the selected page into a section (an existing one,
        a new name, or Unfiled), or rename the selected section (onto an
        existing name = merge into it)."""
        win = self.instruction_editor_window
        row = self._selected_instruction_row()
        if row is None:
            messagebox.showwarning(
                "No selection", "Select an instruction to move, or a section to rename.",
                parent=win)
            return
        kind, key = row
        instructions = self._load_saved_instructions()
        choices = sections(instructions) + [UNFILED_LABEL]
        if kind == "page":
            if key not in instructions:
                return
            answer = self._ask_section(
                "Move to section",
                f"Section for '{key}' — pick one, or type a new name:",
                choices, section_of(instructions[key]) or UNFILED_LABEL)
            changed = answer is not None and file_page(instructions, key, answer)
            select = row
        else:
            if key == UNFILED:
                messagebox.showwarning(
                    UNFILED_LABEL,
                    f"'{UNFILED_LABEL}' holds the instructions not filed in any section "
                    "and cannot be renamed — move its instructions into a section instead.",
                    parent=win)
                return
            answer = self._ask_section(
                "Rename section",
                f"New name for section '{key}' (an existing name merges into that section):",
                choices, key)
            changed = answer is not None and rename_section(instructions, key, answer)
            select = ("section", normalize_section(answer)) if changed else row
        if changed:
            self._save_instructions_to_disk(instructions)
            self._refresh_instruction_list(instructions, select=select)

    def _ask_section(self, title, prompt, choices, initial):
        """A small modal prompt over the editor: a label, an editable
        dropdown pre-filled with `initial` (existing sections and Unfiled to
        pick from, or type a new name), OK / Cancel. Returns the text, or
        None on Cancel. Enter is OK (on a button, that button), Escape
        cancels from anywhere — the one field here holds at most a section
        name, so unlike the editor there is no draft to protect; Alt+O /
        Alt+C press the buttons."""
        parent = self.instruction_editor_window
        dlg = tk.Toplevel(parent)
        dlg.withdraw()
        dlg.title(title)
        dlg.transient(parent)
        dlg.resizable(False, False)
        result = [None]

        tk.Label(dlg, text=prompt, font=("Arial", 10), wraplength=360, justify="left").grid(
            row=0, column=0, sticky="w", padx=15, pady=(12, 4))
        var = tk.StringVar(value=initial)
        combo = ttk.Combobox(dlg, textvariable=var, values=choices, font=("Arial", 10), width=36)
        combo.grid(row=1, column=0, sticky="ew", padx=15, pady=(0, 10))

        def ok(event=None):
            result[0] = var.get()
            dlg.destroy()

        def cancel(event=None):
            dlg.destroy()
            return "break"

        btn_row = tk.Frame(dlg)
        btn_row.grid(row=2, column=0, pady=(0, 12))
        ok_btn = tk.Button(btn_row, text="OK", width=8, command=ok)
        ok_btn.pack(side=tk.LEFT, padx=8)
        cancel_btn = tk.Button(btn_row, text="Cancel", width=8, command=cancel)
        cancel_btn.pack(side=tk.LEFT, padx=8)
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        dlg.bind("<Return>", ok)
        dlg.bind("<KP_Enter>", ok)
        dlg.bind("<Escape>", cancel)
        combo.bind("<Escape>", cancel)   # before the leave-the-field class binding
        bind_mnemonics(dlg, {"o": ok_btn, "c": cancel_btn})

        dlg.update_idletasks()
        self._place_window(dlg, "section_prompt",
                           (dlg.winfo_reqwidth(), dlg.winfo_reqheight()), parent=parent)
        dlg.deiconify()
        combo.focus_set()
        combo.selection_range(0, tk.END)
        dlg.wait_visibility()
        dlg.grab_set()
        dlg.wait_window()
        return result[0]

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
        self.excel_enabled.set(self._editor_excel.get())
        self.meta_enabled.set(self._editor_meta.get())
        self.mcp_enabled.set(self._editor_mcp.get())
        self.google_enabled.set(self._editor_google.get())
        self.proton_enabled.set(self._editor_proton.get())
        self.outlook_enabled.set(self._editor_outlook.get())
        self.conversational_enabled.set(self._editor_conversational.get())
        self.agent_instruction = text
        self.agent_instruction_name = name
        # Persist to disk. The entry is rebuilt from the editor, so the two
        # list-layout keys (instruction_layout.py: "section" / "order") are
        # carried over from the existing entry; a NEW name is filed into the
        # section the list's selection is in (else unfiled) as its last page.
        instructions = self._load_saved_instructions()
        existing = instructions.get(name)
        instructions[name] = {
            "text": text,
            "images": [
                {"data": d, "media_type": mt, "filename": fn}
                for d, mt, fn in self.pending_images
            ],
            "desktop": self.desktop_enabled.get(),
            "browser": self.browser_enabled.get(),
            "excel": self.excel_enabled.get(),
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
            "blocked_tools": sorted(getattr(self, "_blocked_tools", [])),
        }
        entry = instructions[name]
        if existing:
            for key in ("section", "order"):
                if key in existing:
                    entry[key] = existing[key]
        else:
            section = self._section_for_new_page(instructions)
            if section:
                entry["section"] = section
            renumber(instructions, rows(instructions))   # lands last in its section
        self._save_instructions_to_disk(instructions)
        self._instr_shown_name = name
        self._refresh_instruction_list(instructions, select=("page", name))
        self._update_title()
        self._save_last_state()

    def _delete_instruction(self):
        row = self._selected_instruction_row()
        if row and row[0] == "section":
            messagebox.showwarning(
                "Section selected",
                "Select an instruction, not a section. A section is just the "
                "instructions filed in it — it disappears once they are moved out "
                "or deleted.", parent=self.instruction_editor_window)
            return
        name = row[1] if row else ""
        if not name:
            name = self._instr_name_entry.get().strip()
        if not name:
            messagebox.showwarning("No selection", "Select or enter an instruction name to delete.", parent=self.instruction_editor_window)
            return
        instructions = self._load_saved_instructions()
        if name not in instructions:
            messagebox.showwarning("Not found", f"No saved instruction named '{name}'.", parent=self.instruction_editor_window)
            return
        # GUI-only guard (2026-09-02): a click on DELETE is one slip away from
        # losing an instruction from the OneDrive-shared store on every
        # machine, with no undo. The manage_instructions tool's delete branch
        # is deliberately NOT gated — tool-driven deletes are the model acting
        # on an explicit instruction, and an unattended run has nobody to
        # answer a dialog.
        if not messagebox.askyesno(
                "Delete instruction",
                f"Permanently delete the saved instruction '{name}'?\n\n"
                "It is removed from the shared store on every synced machine. "
                "This cannot be undone.",
                icon="warning", default="no", parent=self.instruction_editor_window):
            return
        instructions.pop(name)
        self._save_instructions_to_disk(instructions)
        if getattr(self, "_instr_shown_name", "") == name:
            self._instr_shown_name = ""
        self._refresh_instruction_list(instructions)   # its row goes, and the selection with it
        self._instr_name_entry.delete(0, tk.END)

    def _clear_instruction_editor(self):
        self._instr_text.delete("1.0", tk.END)
        self._instr_name_entry.delete(0, tk.END)
        self._instr_shown_name = ""
        self._select_instruction_row(None)
        self._editor_images.clear()
        self._editor_desktop.set(False)
        self._editor_browser.set(False)
        self._editor_excel.set(False)
        self._editor_meta.set(False)
        self._editor_mcp.set(False)
        self._editor_google.set(False)
        self._editor_proton.set(False)
        self._editor_outlook.set(False)
        self._editor_conversational.set(False)
        self._disabled_confirm_patterns = set()
        self._blocked_tools = set()
        self._update_ps_safety_button()
        # Reset model controls to defaults
        if self._has_anthropic:
            default_provider = "Anthropic"
            default_model = DEFAULT_MODEL
        elif self._has_openai:
            default_provider = "OpenAI"
            default_model = OPENAI_DEFAULT_MODEL
        elif self._has_gemini:
            default_provider = "Google"
            default_model = GEMINI_DEFAULT_MODEL
        elif self._has_xai:
            default_provider = "xAI"
            default_model = XAI_DEFAULT_MODEL
        elif self._has_kimi:
            default_provider = "Moonshot"
            default_model = KIMI_DEFAULT_MODEL
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

    def _on_instruction_selected(self, event=None):
        """<<TreeviewSelect>> on the Instructions list: load the selected
        page into the editor. Tk QUEUES this event (it fires later, and every
        pending one sees the final selection), so a flag around the list's
        own programmatic selections could not silence it; instead the load is
        skipped when the selected page is the one the editor already shows
        (`_instr_shown_name`) — which is also what keeps a live, applied-but-
        unsaved draft from being replaced by its disk copy when the editor
        opens on it. Selecting a section header loads nothing."""
        name = self._selected_instruction_name()
        if not name or name == getattr(self, "_instr_shown_name", ""):
            return
        instructions = self._load_saved_instructions()
        if name in instructions:
            self._instr_shown_name = name
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
            self._editor_excel.set(entry.get("excel", False))
            self._editor_meta.set(entry.get("meta", False))
            self._editor_mcp.set(entry.get("mcp", False))
            self._editor_google.set(entry.get("google", False))
            self._editor_proton.set(entry.get("proton", False))
            self._editor_outlook.set(entry.get("outlook", False))
            self._editor_conversational.set(entry.get("conversational", False))
            self._restore_model_params(entry)
            self._restore_skill_modes(entry)
            self._disabled_confirm_patterns = set(entry.get("disabled_confirm_patterns", []))
            self._blocked_tools = set(entry.get("blocked_tools", []))
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
        self.excel_enabled.set(self._editor_excel.get())
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
