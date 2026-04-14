import tkinter as tk
from tkinter import ttk

from myagent.constants import (
    MONO_FONT, FALLBACK_MODELS, DEFAULT_MODEL, OPENAI_DEFAULT_MODEL,
    GEMINI_DEFAULT_MODEL, OLLAMA_DEFAULT_MODEL, ADAPTIVE_THINKING_MODELS,
    MANUAL_THINKING_PREFIXES, EFFORT_LEVELS, ADAPTIVE_MODE_VALUES,
    ADAPTIVE_MODE_VALUES_NO_MAX, BUDGET_PRESETS, GEMINI_THINKING_PREFIXES,
    OLLAMA_THINKING_PREFIXES,
)


class UIMixin:

    # ── UI Setup ────────────────────────────────────────────────────────

    def setup_ui(self):
        self.root.grid_rowconfigure(0, weight=0)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_rowconfigure(2, weight=0)
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=0)

        # Initialize StringVars for model controls (widgets created in editor)
        self._provider_var = tk.StringVar(value=self.provider)
        self._model_id_list = self.available_models
        self._model_var = tk.StringVar(value=self._get_display_name(self.model))
        self._temp_var = tk.DoubleVar(value=self.temperature)
        self._thinking_var = tk.BooleanVar(value=False)
        self._thinking_strength_var = tk.StringVar(value="high")
        self._thinking_mode_var = tk.StringVar(value="Off")
        self._text_verbosity_var = tk.StringVar(value="Medium")

        # No model widgets until editor is opened
        self._provider_combo = None
        self._model_combo = None
        self._temp_label = None
        self._temp_spin = None
        self._thinking_check = None
        self._thinking_strength_combo = None
        self._thinking_mode_combo = None
        self._verbosity_label = None
        self._verbosity_combo = None

        # Row 0: Chat toolbar — START/STOP + Instruction + model info + Save
        chat_toolbar = tk.Frame(self.root)
        chat_toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 0))

        self._start_button = tk.Button(
            chat_toolbar, text="START", command=self._start_agent, width=8,
            font=("Arial", 10, "bold"),
        )
        self._start_button.pack(side=tk.LEFT, padx=(0, 5))

        self._stop_button = tk.Button(
            chat_toolbar, text="STOP", command=self._stop_agent, width=8,
            font=("Arial", 10, "bold"), state="disabled",
        )
        self._stop_button.pack(side=tk.LEFT, padx=(0, 8))

        self.instruction_button = tk.Button(
            chat_toolbar, text="Instruction", command=self.open_instruction_editor,
        )
        self.instruction_button.pack(side=tk.LEFT, padx=(0, 8))

        self._update_title()

        tk.Label(chat_toolbar, text="Save Chat as", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        self.chat_name_entry = tk.Entry(chat_toolbar, font=("Arial", 10))
        self.chat_name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        # Row 1: Chat display
        self.chat_display = tk.Text(
            self.root, wrap=tk.WORD, state="disabled", font=("Arial", 11)
        )
        self.chat_display.grid(row=1, column=0, sticky="nsew", padx=(10, 0), pady=10)

        scrollbar = tk.Scrollbar(self.root, command=self.chat_display.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", pady=10, padx=(0, 10))
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
            "warning", foreground="#e65100", font=("Arial", 10, "italic")
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

        # Row 2: Checkbox row
        checkbox_frame = tk.Frame(self.root)
        checkbox_frame.grid(row=2, column=0, columnspan=2, pady=(0, 5))

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

        self.diag_toggle = tk.Checkbutton(
            checkbox_frame, text="Diag", variable=self.diag_enabled,
            font=("Arial", 9),
        )
        self.diag_toggle.pack(side=tk.LEFT, padx=(5, 0))


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

    def _has_model_widgets(self):
        """Return True if the editor model widgets currently exist."""
        return self._model_combo is not None

    def _on_provider_changed(self, event=None):
        """Handle provider combobox selection change."""
        new_provider = self._provider_var.get()
        if new_provider == self.provider:
            return
        self.provider = new_provider
        # Refresh model list for the new provider
        self.available_models = self._fetch_models_for_provider()
        self._model_id_list = self.available_models
        display_names = [self._get_display_name(mid) for mid in self._model_id_list]
        if self._has_model_widgets():
            self._model_combo["values"] = display_names
        # Select default model for new provider
        if new_provider == "OpenAI":
            default = OPENAI_DEFAULT_MODEL
        elif new_provider == "Gemini":
            default = GEMINI_DEFAULT_MODEL
        elif new_provider == "Ollama":
            default = OLLAMA_DEFAULT_MODEL
        else:
            default = DEFAULT_MODEL
        if default in self.available_models:
            self.model = default
        elif self.available_models:
            self.model = self.available_models[0]
        self._model_var.set(self._get_display_name(self.model))
        self._on_model_selected()
        self._update_title()
        self._save_last_state()

    def _forget_all_model_widgets(self):
        """Hide all model parameter widgets to reset pack order."""
        if not self._has_model_widgets():
            return
        for w in (self._temp_label, self._temp_spin,
                  self._thinking_check, self._thinking_strength_combo,
                  self._thinking_mode_label, self._thinking_mode_combo,
                  self._verbosity_label, self._verbosity_combo):
            w.pack_forget()

    def _on_model_selected(self, event=None):
        selected_display = self._model_var.get()
        for mid in self._model_id_list:
            if self._get_display_name(mid) == selected_display:
                self.model = mid
                break
        support = self._model_supports_thinking()
        if self._has_model_widgets():
            # Reset all model param widgets to guarantee correct pack order
            self._forget_all_model_widgets()
            if support == "adaptive" and self.provider == "Anthropic":
                # Adaptive model (Anthropic): show mode combo
                self._thinking_mode_label.config(text="Thinking")
                self._thinking_mode_label.pack(side=tk.LEFT, padx=(10, 5))
                self._thinking_mode_combo.pack(side=tk.LEFT, padx=(0, 10))
                # Populate values: Max only for Opus 4.6
                if self.model == "claude-opus-4-6":
                    self._thinking_mode_combo["values"] = ADAPTIVE_MODE_VALUES
                else:
                    self._thinking_mode_combo["values"] = ADAPTIVE_MODE_VALUES_NO_MAX
                    if self._thinking_mode_var.get() == "Max":
                        self._thinking_mode_var.set("High")
                # Sync state from thinking_mode (may pack temp after combo)
                self._on_thinking_mode_changed()
            elif support == "extended" and self.provider == "OpenAI":
                # GPT-5.1+: show mode combobox with None/Low/.../Xhigh
                self._thinking_mode_label.config(text="Reasoning")
                self._thinking_mode_label.pack(side=tk.LEFT, padx=(10, 5))
                self._thinking_mode_combo.pack(side=tk.LEFT, padx=(0, 10))
                # Build values based on model capabilities
                values = ["None", "Low", "Medium", "High"]
                if self._has_reasoning_xhigh():
                    values.append("Xhigh")
                self._thinking_mode_combo["values"] = values
                # Validate current selection
                current = self._thinking_mode_var.get()
                if current not in values:
                    self._thinking_mode_var.set("None")
                self._on_thinking_mode_changed()
                # Show verbosity after mode combo
                self._verbosity_label.pack(side=tk.LEFT, padx=(10, 5))
                self._verbosity_combo.pack(side=tk.LEFT, padx=(0, 10))
            elif support is not None:
                # Manual thinking model or OpenAI/Gemini reasoning: show checkbox + strength.
                # Ollama is an exception — its `think` flag is boolean-only today,
                # so we hide the strength combo and show only the checkbox.
                self._thinking_check.pack(side=tk.LEFT, padx=(10, 2))
                self._thinking_check.config(state="normal")
                self._update_thinking_strength_options()
                if self.thinking_enabled:
                    if self.provider != "Ollama":
                        self._thinking_strength_combo.pack(side=tk.LEFT, padx=(0, 10))
                        self._thinking_strength_combo.config(state="readonly")
                    if self.provider in ("Gemini", "Ollama"):
                        self._temp_label.pack(side=tk.LEFT, padx=(10, 5))
                        self._temp_spin.pack(side=tk.LEFT, padx=(0, 10))
                else:
                    if not (self.provider == "OpenAI" and self._is_openai_reasoning_model()):
                        self._temp_label.pack(side=tk.LEFT, padx=(10, 5))
                        self._temp_spin.pack(side=tk.LEFT, padx=(0, 10))
                # Show verbosity for gpt-5.0 family
                if self._has_openai_verbosity():
                    self._verbosity_label.pack(side=tk.LEFT, padx=(10, 5))
                    self._verbosity_combo.pack(side=tk.LEFT, padx=(0, 10))
            else:
                # No thinking support
                self._thinking_var.set(False)
                self.thinking_enabled = False
                self.thinking_mode = "off"
                # gpt-5.x-chat Instant models don't support temperature
                if not self._is_gpt5_chat_model():
                    self._temp_label.pack(side=tk.LEFT, padx=(10, 5))
                    self._temp_spin.pack(side=tk.LEFT, padx=(0, 10))
                # gpt-5.x-chat Instant models support verbosity
                if self._has_openai_verbosity():
                    self._verbosity_label.pack(side=tk.LEFT, padx=(10, 5))
                    self._verbosity_combo.pack(side=tk.LEFT, padx=(0, 10))
        else:
            # No widgets — just update state
            if support is None:
                self._thinking_var.set(False)
                self.thinking_enabled = False
                self.thinking_mode = "off"
        self._update_title()
        self._save_last_state()

    def _on_temp_changed(self):
        try:
            val = self._temp_var.get()
            self.temperature = max(0.0, min(1.0, val))
        except (tk.TclError, ValueError):
            self.temperature = 1.0
        self._temp_var.set(self.temperature)
        self._update_title()
        self._save_last_state()

    def _model_supports_thinking(self, model_id=None):
        mid = model_id or self.model
        if self.provider == "OpenAI":
            if self._is_openai_reasoning_model(mid):
                if self._has_reasoning_none(mid):
                    return "extended"
                return "adaptive"
            return None
        if self.provider == "Gemini":
            return "adaptive" if self._is_gemini_thinking_model(mid) else None
        if self.provider == "Ollama":
            # Qwen3 / DeepSeek-R1 / gpt-oss get the manual checkbox+strength UI.
            # Strength is display-only for now — Ollama's `think` flag is
            # boolean-only; all effort levels map to think=True until upstream
            # exposes a per-request budget.
            return "manual" if any(mid.startswith(p) for p in OLLAMA_THINKING_PREFIXES) else None
        if mid in ADAPTIVE_THINKING_MODELS:
            return "adaptive"
        for prefix in MANUAL_THINKING_PREFIXES:
            if mid.startswith(prefix):
                return "manual"
        return None

    def _is_gemini_thinking_model(self, model_id=None):
        mid = model_id or self.model
        if "lite" in mid:
            return False
        return any(mid.startswith(p) for p in GEMINI_THINKING_PREFIXES)

    def _restore_model_params(self, entry, state_file=False):
        """Restore provider, model, temperature, and thinking settings from an instruction entry or state file."""
        has_widgets = self._has_model_widgets()
        # Restore provider first (model list depends on it)
        provider_key = "provider"
        saved_provider = entry.get(provider_key, "Anthropic")
        if saved_provider != self.provider:
            can_switch = (saved_provider == "Anthropic" and self._has_anthropic) or \
                         (saved_provider == "OpenAI" and self._has_openai) or \
                         (saved_provider == "Gemini" and self._has_gemini) or \
                         (saved_provider == "Ollama" and self._has_ollama)
            if can_switch:
                self.provider = saved_provider
                self._provider_var.set(saved_provider)
                self.available_models = self._fetch_models_for_provider()
                self._model_id_list = self.available_models
                display_names = [self._get_display_name(mid) for mid in self._model_id_list]
                if has_widgets:
                    self._model_combo["values"] = display_names
        # Restore model (fall back to first available if saved model doesn't match provider)
        model_key = "last_model" if state_file else "model"
        model = entry.get(model_key, "")
        if model and model in self.available_models:
            self.model = model
            self._model_var.set(self._get_display_name(model))
        elif self.available_models:
            self.model = self.available_models[0]
            self._model_var.set(self._get_display_name(self.model))
        # Restore temperature
        temp = entry.get("temperature")
        if temp is not None:
            self.temperature = max(0.0, min(1.0, float(temp)))
            self._temp_var.set(self.temperature)
        # Restore thinking
        if "thinking_enabled" in entry:
            self.thinking_enabled = entry["thinking_enabled"]
            self.thinking_effort = entry.get("thinking_effort", "high")
            self.thinking_budget = entry.get("thinking_budget", 8192)
            # Restore thinking_mode with backward compat from old entries
            if "thinking_mode" in entry:
                self.thinking_mode = entry["thinking_mode"]
            elif self.thinking_enabled:
                # Old format: map thinking_enabled + thinking_effort → thinking_mode
                self.thinking_mode = self.thinking_effort  # low/medium/high/max
            else:
                self.thinking_mode = "off"
            self._thinking_var.set(self.thinking_enabled)
            # Capitalize thinking_mode for display; handle special values
            if self.thinking_mode == "off":
                self._thinking_mode_var.set("Off")
            elif self.thinking_mode == "none":
                self._thinking_mode_var.set("None")
            else:
                self._thinking_mode_var.set(self.thinking_mode.capitalize())
            support = self._model_supports_thinking()
            if support is None:
                self._thinking_var.set(False)
                self.thinking_enabled = False
                self.thinking_mode = "off"
                self._thinking_mode_var.set("Off")
            elif support == "adaptive" and self.provider == "Anthropic":
                # Adaptive model: _on_model_selected will show/hide correct widgets
                pass
            elif support == "extended":
                # GPT-5.1+: _on_model_selected will show mode combobox
                pass
            else:
                # Manual or OpenAI: restore strength combo
                if support == "adaptive":
                    self._thinking_strength_var.set(self.thinking_effort)
                elif support == "manual":
                    for k, v in BUDGET_PRESETS.items():
                        if v == self.thinking_budget:
                            self._thinking_strength_var.set(k)
                            break
            # Let _on_model_selected handle widget visibility and state
            if has_widgets:
                self._on_model_selected()
            else:
                self._on_thinking_toggled()
        # Restore text verbosity
        self.text_verbosity = entry.get("text_verbosity", "medium")
        self._text_verbosity_var.set(self.text_verbosity.capitalize())
        self._update_title()

    def _on_thinking_toggled(self):
        self.thinking_enabled = self._thinking_var.get()
        if self._has_model_widgets():
            # Reset and re-pack all widgets in correct order via _on_model_selected
            self._on_model_selected()
        self._save_last_state()

    def _update_thinking_strength_options(self):
        if not self._has_model_widgets():
            return
        support = self._model_supports_thinking()
        if support == "adaptive":
            if self.provider == "OpenAI" and self._is_gpt5_family() and self._parse_gpt5_minor() == 0:
                values = ["minimal", "low", "medium", "high"]
            elif self.provider in ("OpenAI", "Gemini"):
                values = ["low", "medium", "high"]
            else:
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
        self._update_title()
        self._save_last_state()

    def _on_thinking_mode_changed(self):
        """Handle adaptive thinking mode combobox selection."""
        val = self._thinking_mode_var.get()
        mode = val.lower()  # "off", "none", "adaptive", "low", "medium", "high", "max", "xhigh"
        self.thinking_mode = mode
        if mode in ("off", "none"):
            self.thinking_enabled = False
            self._thinking_var.set(False)
            self.thinking_effort = mode  # "off" for Anthropic, "none" for OpenAI
        else:
            self.thinking_enabled = True
            self._thinking_var.set(True)
            if mode == "adaptive":
                self.thinking_effort = "high"  # internal default, but not sent to API
            else:
                self.thinking_effort = mode  # low/medium/high/max/xhigh/minimal
        # Update temp visibility — pack right after mode combo to maintain order
        # (temp_label and temp_spin were already forgotten by _forget_all_model_widgets,
        #  so packing here places them directly after the mode combo)
        if self._has_model_widgets():
            show_temp = False
            if mode in ("off", "none"):
                if self.provider != "OpenAI" or not self._is_gpt5_family():
                    show_temp = True  # non-gpt5 models
                elif mode == "none" and self._gpt5_supports_temp_at_none():
                    show_temp = True  # gpt-5.4+ with effort=none
            if show_temp:
                self._temp_label.pack(side=tk.LEFT, padx=(10, 5))
                self._temp_spin.pack(side=tk.LEFT, padx=(0, 10))
            else:
                self._temp_label.pack_forget()
                self._temp_spin.pack_forget()
        self._update_title()
        self._save_last_state()

    def _on_verbosity_changed(self):
        self.text_verbosity = self._text_verbosity_var.get().lower()
        self._update_title()
        self._save_last_state()

    def _get_model_param_summary(self):
        """Build a compact string of the parameters actually in effect for
        the current provider/model. Mirrors the per-model widget-visibility
        logic in _on_model_selected so the title reflects what's truly being
        sent to the API — not a generic dump of every possible parameter."""
        parts = []
        support = self._model_supports_thinking()
        mode = (self.thinking_mode or "").lower()

        if support == "adaptive" and self.provider == "Anthropic":
            parts.append(f"mode={mode.capitalize() or 'Off'}")
            if mode == "off":
                parts.append(f"temp={self.temperature:g}")
        elif support == "extended" and self.provider == "OpenAI":
            parts.append(f"reasoning={mode.capitalize() or 'None'}")
            if self._has_openai_verbosity():
                parts.append(f"verbosity={self.text_verbosity}")
            if mode in ("", "none") and self._gpt5_supports_temp_at_none():
                parts.append(f"temp={self.temperature:g}")
        elif support is not None:
            if self.thinking_enabled:
                if self.provider == "Ollama":
                    # Ollama's `think` flag is boolean-only — no strength knob.
                    parts.append("thinking=on")
                elif support == "manual" and self.provider == "Anthropic":
                    # Anthropic manual (Haiku 4.5 / Sonnet 4.5 / older) uses a
                    # token budget (1K/4K/8K/16K/32K). thinking_effort is stale
                    # for these — use thinking_budget reverse-mapped to its label.
                    label = next(
                        (k for k, v in BUDGET_PRESETS.items() if v == self.thinking_budget),
                        f"{self.thinking_budget}tok",
                    )
                    parts.append(f"thinking={label}")
                else:
                    # Gemini / OpenAI adaptive (gpt-5 reasoning pre-5.1) — effort string
                    parts.append(f"thinking={self.thinking_effort}")
                if self.provider in ("Gemini", "Ollama"):
                    parts.append(f"temp={self.temperature:g}")
            else:
                parts.append("thinking=off")
                if not (self.provider == "OpenAI" and self._is_openai_reasoning_model()):
                    parts.append(f"temp={self.temperature:g}")
            if self._has_openai_verbosity():
                parts.append(f"verbosity={self.text_verbosity}")
        else:
            if not self._is_gpt5_chat_model():
                parts.append(f"temp={self.temperature:g}")
            if self._has_openai_verbosity():
                parts.append(f"verbosity={self.text_verbosity}")

        return " ".join(parts)

    def _update_title(self):
        model_display = self._get_display_name(self.model)
        param_summary = self._get_model_param_summary()
        param_suffix = f" | {param_summary}" if param_summary else ""
        model_info = f"{self.provider} / {model_display}{param_suffix}"
        inst_num = getattr(self, '_instance_num', 1)
        suffix = f" ({inst_num})" if inst_num > 1 else ""
        if self.agent_instruction_name:
            self.root.title(f"My Agent{suffix} — {self.agent_instruction_name}  [{model_info}]")
        else:
            self.root.title(f"My Agent{suffix}  [{model_info}]")
