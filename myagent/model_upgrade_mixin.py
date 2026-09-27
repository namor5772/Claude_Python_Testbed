"""Model upgrade for the rest of a run (mixin #26, 2026-09-27).

A run started on a cheap model — Sonnet 5 following a set of skills, say —
ends at the Agent Request dialog, where the user typically asks the agent to
look back over the run just had and tighten the skills it used. That is the
task a stronger model does better, so the dialog carries an **Upgrade** box:
ticked, the reply is sent as usual and every API call FROM THAT REPLY ON goes
to the upgrade model with its own thinking level; the conversation, the tools,
the system prompt and the run's other settings (temperature, verbosity, Fast)
are untouched. The switch lasts for the rest of the run; it never outlives it
and never reaches the applied instruction, the state file or the store.

WHICH model is a setting of the INSTRUCTION (the user's decision, the day it
shipped: it was per-user at first, like Voice Setup): `upgrade_target` in the
instruction entry, `{"provider", "model", "level"}`, carried through every
site the other per-instruction settings pass (the editor's SAVE / select /
CLEAR, the applied snapshot, `_apply_instruction_entry`, manage_instructions)
exactly like the Auto-send tick, `dictation_auto_send`. It is edited in
**Model Setup**, a button beside Voice Setup at the bottom left of the main
window, whose Save writes the value through to the instruction's store entry
at once (a targeted key write, the Auto-send way). An instruction without one
gets its provider's default (UPGRADE_DEFAULT_TARGETS); a model of "" is an
explicit "no upgrade"; a target saved for another provider (the instruction's
provider was changed since) does not apply.

A run upgrades within its own provider only — the history's provider-specific
reasoning artefacts stay readable there (live-probed 2026-09-27: a Sonnet 5
thinking block replayed to Fable 5.1 or Opus 5, a Gemini 3.8 Flash thought
signature replayed to 3.1 Pro, a gpt-5.6-terra function call replayed to
gpt-6-astra — all HTTP 200 with no history edit; stripping a Gemini signature
is the one thing that FAILS, so nothing is ever stripped).

Mechanics. `_upgrade_apply` runs on the streaming worker inside
`do_user_prompt`, after the reply is captured and before the worker returns to
the loop, so the next call is the first upgraded one: it stashes the run's
model fields in `_upgrade_original`, THEN rewrites the live ones (the stash is
set first because `_save_last_state`, on the Tk thread every five seconds,
writes the stash while it exists — the state file must go on describing the
instruction's model, or a relaunch would come back on the expensive one), and
posts a line to the output pane. `_upgrade_end_run` puts the originals back
when the loop ends (both tails of stream_worker), after the cost log line has
been written under the upgraded model with an `upgraded-from=<model>@call<N>`
part in its PARAMETERS field. The thinking level is turned into the four live
thinking fields by `_upgrade_params_for`, which mirrors the instruction
editor's three handlers; the levels offered for a model come from the same
per-family helpers the editor's combobox uses, evaluated on a PROBE (a bare
instance carrying only provider + model + the fetched capability tables),
never on the live app.
"""

import tkinter as tk
from tkinter import ttk

from myagent.constants import (
    BUDGET_PRESETS, PROVIDERS, UPGRADE_DEFAULT_TARGETS, UPGRADE_NO_MODEL_LABEL,
)
from myagent.keyboard import bind_mnemonics

UPGRADE_MUTED_FG = "#555555"
UPGRADE_ERROR_FG = "#b00020"
# The live model fields a switch rewrites and restores. `provider` is not one
# of them: an upgrade stays within the run's provider.
UPGRADE_FIELDS = ("model", "thinking_enabled", "thinking_effort",
                  "thinking_budget", "thinking_mode")
UPGRADE_NOTE = ("Ticked in an Agent Request dialog, the upgrade model answers from that "
                "reply to the end of the run — same provider, same conversation, same "
                "tools; temperature, verbosity and Fast keep the run's settings. The "
                "cost log records the run under the upgrade model with an "
                "upgraded-from part.")


class ModelUpgradeMixin:

    # ── The instruction's setting (pure: no Tk, no network) ────────────

    @staticmethod
    def _upgrade_sanitize_target(raw, provider=None):
        """An instruction entry's `upgrade_target` → {"provider", "model",
        "level"}, or None when it names none (absent or malformed: the
        provider default applies). A model of "" is an explicit "no upgrade"
        (its level is then ""). `provider` fills a value that carries none
        (manage_instructions passes the entry's own)."""
        if not isinstance(raw, dict):
            return None
        chosen = raw.get("provider") or provider
        if chosen not in PROVIDERS:
            return None
        model = raw.get("model")
        model = model.strip() if isinstance(model, str) else ""
        level = raw.get("level")
        level = level.strip() if isinstance(level, str) and model else ""
        return {"provider": chosen, "model": model, "level": level}

    def _upgrade_own_target(self):
        """The live instruction's own setting for the run's provider, or None
        when it has none for it (never set, or set before its provider was
        changed)."""
        own = self._upgrade_sanitize_target(getattr(self, "upgrade_target", None))
        return own if own is not None and own["provider"] == self.provider else None

    def _upgrade_target(self):
        """The upgrade a run of the live instruction gets: {"model", "level"}
        — the instruction's own, else its provider's default — or None for
        no upgrade (an explicit "no upgrade", or no default for the
        provider)."""
        own = self._upgrade_own_target()
        if own is not None:
            return {"model": own["model"], "level": own["level"]} if own["model"] else None
        default = UPGRADE_DEFAULT_TARGETS.get(self.provider)
        return dict(default) if default else None

    def _upgrade_instruction_name(self):
        """The saved instruction whose settings are LIVE, which is the one
        Model Setup edits: the page the Instruction Editor shows while it is
        open (selecting a page there restores its model settings at once —
        the upgrade model among them — before any Apply), else the applied
        instruction; "" for one not saved yet (the value then lives in the
        session and the applied snapshot until the editor's SAVE)."""
        editor = getattr(self, "instruction_editor_window", None)
        try:
            if editor is not None and editor.winfo_exists():
                return getattr(self, "_instr_shown_name", "") or ""
        except tk.TclError:
            pass
        return getattr(self, "agent_instruction_name", "") or ""

    def _upgrade_set_target(self, value):
        """Model Setup's Save: the live value, then written through to the
        instruction's store entry — a targeted key write, nothing else in the
        entry is touched, and a value it already holds is not rewritten (no
        OneDrive churn) — and to the applied snapshot (`_save_last_state`),
        exactly as the Auto-send tick is kept."""
        self.upgrade_target = value
        name = self._upgrade_instruction_name()
        if name:
            instructions = self._load_saved_instructions()
            entry = instructions.get(name)
            if isinstance(entry, dict) and entry.get("upgrade_target") != value:
                entry["upgrade_target"] = value
                self._save_instructions_to_disk(instructions)
        self._save_last_state()

    # ── Levels: what a model offers, and what a level means ───────────

    def _upgrade_probe(self, provider, model):
        """A bare instance of this app's class carrying only what the
        per-family detection helpers read — provider, model, the capability
        tables the live fetches filled — so a model can be asked what it
        supports without touching the live app's fields."""
        probe = object.__new__(type(self))
        probe.provider = provider
        probe.model = model
        probe._xai_caps = dict(getattr(self, "_xai_caps", None) or {})
        probe._kimi_caps = dict(getattr(self, "_kimi_caps", None) or {})
        probe._anthropic_unsupported = set(getattr(self, "_anthropic_unsupported", ()))
        probe.thinking_effort = "high"
        probe.thinking_mode = "adaptive"
        return probe

    def _upgrade_level_values(self, provider, model):
        """The thinking levels Model Setup offers for a model — the rungs the
        instruction editor's own controls would show for it, with an "Off"
        (or the boolean "On") where the editor uses a checkbox, and [] for a
        model with no thinking control at all. Lower-cased, each is what
        `_upgrade_params_for` reads."""
        probe = self._upgrade_probe(provider, model)
        support = probe._model_supports_thinking()
        if support is None:
            return []
        if provider == "Anthropic":
            if support == "adaptive":
                return list(probe._anthropic_mode_values())
            return ["Off"] + list(BUDGET_PRESETS)          # manual budgets
        if provider == "OpenAI":
            if support == "extended":
                return list(probe._openai_reasoning_values())
            if probe._is_gpt5_family() and probe._parse_gpt5_minor() == 0:
                return ["Off", "Minimal", "Low", "Medium", "High"]
            return ["Off", "Low", "Medium", "High"]        # o-series strength
        if provider == "xAI":
            return [v.capitalize() for v in probe._xai_reasoning_values() or []]
        if provider == "Moonshot":
            if support == "extended":
                return [v.capitalize() for v in probe._kimi_reasoning_values()]
            return ["Off", "On"]                            # k2.6 / k2.5 toggle
        if provider == "Google":
            return ["Off", "Low", "Medium", "High"]
        return ["Off", "On"]                                # Ollama's boolean think

    def _upgrade_level_for(self, provider, model, level):
        """`level` if the model offers it (case-insensitively), else the
        model's strongest level — a saved Max on a model that stops at
        Xhigh runs at Xhigh; "" for a model with no thinking control."""
        values = self._upgrade_level_values(provider, model)
        if not values:
            return ""
        wanted = (level or "").strip().lower()
        for value in values:
            if value.lower() == wanted:
                return value
        return values[-1]

    def _upgrade_params_for(self, provider, model, level):
        """The four live thinking fields for a model at `level`, exactly as
        the instruction editor's handlers would leave them: the mode-combobox
        kinds (`_on_thinking_mode_changed`: Off / None disable, Adaptive
        enables at the internal default effort, a rung is both the mode and
        the effort); the checkbox + strength kinds (`_on_thinking_toggled` +
        `_on_thinking_strength_changed`: enabled + effort, with the mode the
        legacy inference in `_restore_model_params` would give it); the
        budget kind (`BUDGET_PRESETS`); and the boolean On/Off kinds."""
        level = self._upgrade_level_for(provider, model, level)
        low = level.lower()
        params = {"thinking_enabled": getattr(self, "thinking_enabled", False),
                  "thinking_effort": getattr(self, "thinking_effort", "high"),
                  "thinking_budget": getattr(self, "thinking_budget", 8192),
                  "thinking_mode": getattr(self, "thinking_mode", "off")}
        if not level:
            params.update(thinking_enabled=False, thinking_mode="off")
            return params
        if low in ("off", "none"):
            params.update(thinking_enabled=False, thinking_effort=low, thinking_mode=low)
            return params
        params["thinking_enabled"] = True
        if low == "adaptive":
            params.update(thinking_effort="high", thinking_mode="adaptive")
        elif level in BUDGET_PRESETS:
            params["thinking_budget"] = BUDGET_PRESETS[level]
            params["thinking_mode"] = params["thinking_effort"]
        elif low == "on":
            params["thinking_mode"] = params["thinking_effort"]
        else:
            params.update(thinking_effort=low, thinking_mode=low)
        return params

    # ── The switch ──────────────────────────────────────────────────────

    def _upgrade_active(self):
        return getattr(self, "_upgrade_original", None) is not None

    def _upgrade_apply(self, target):
        """Switch the run to `target` ({"model", "level"}) from the next API
        call on. Worker thread (inside do_user_prompt, after the reply is
        captured). Idempotent: a run upgrades once."""
        if self._upgrade_active() or not target or not target.get("model"):
            return False
        original = {field: getattr(self, field) for field in UPGRADE_FIELDS}
        original["call"] = getattr(self, "_run_call_num", 0)
        params = self._upgrade_params_for(self.provider, target["model"], target.get("level"))
        # The stash goes up BEFORE the live fields change: _save_last_state
        # (Tk thread, periodic) reads the stash while it exists, so no save
        # can ever catch the upgraded model as the applied one.
        self._upgrade_original = original
        self.model = target["model"]
        for field, value in params.items():
            setattr(self, field, value)
        level = self._upgrade_level_for(self.provider, self.model, target.get("level"))
        self.queue.put({"type": "warning", "content": (
            f"⬆ Model upgraded for the rest of this run: {original['model']} → "
            f"{self.model}{f' ({level})' if level else ''}, from call "
            f"#{original['call'] + 1} on.\n")})
        for note in self._upgrade_pricing_warnings():
            self.queue.put({"type": "warning", "content": f"⚠ {note}\n"})
        root = getattr(self, "root", None)
        if root is not None:
            root.after(0, self._update_title)
        return True

    def _upgrade_pricing_warnings(self):
        """The run-start pricing checks (streaming_mixin), for the model the
        run has just moved to — the run-start ones only ever saw the model it
        started on. A model priced by a family catch-all row gets that
        warning as it stands; a model with NO row gets its own text here,
        because the run-start one ("the run will NOT be written to the cost
        log") is wrong mid-run: the calls before the switch were priced, so
        the run IS logged — with a cost that leaves out every call from here
        on, while its token fields still count them. Same exemptions and the
        same fast-table rule, by asking `_unpriced_model_warning` whether it
        would warn."""
        notes = []
        generic = self._generic_pricing_warning(self.provider, self.model)
        if generic:
            notes.append(generic)
        fast = (self.provider == "Anthropic" and getattr(self, "fast_mode", False)
                and self._anthropic_fast_active())
        if self._unpriced_model_warning(self.provider, self.model, fast=fast):
            table = "ANTHROPIC_FAST_PRICING" if fast else f"{self.provider} pricing table"
            notes.append(
                f"{self.model} has no row in the {table} (myagent/constants.py), so "
                f"its calls from here on cannot be priced: they show token counts "
                f"only, and this run's cost-log row will count only the calls "
                f"before the switch — its cost will be too low. Add the model's row.")
        return notes

    def _upgrade_end_run(self):
        """The loop has ended: put the run's own model fields back, so the
        next START — and the editor — see the instruction's model."""
        original = getattr(self, "_upgrade_original", None)
        if original is None:
            return
        self._upgrade_original = None
        for field in UPGRADE_FIELDS:
            setattr(self, field, original[field])
        self._tool_info(f"Model restored to {self.model} for the next run.\n")
        root = getattr(self, "root", None)
        if root is not None:
            root.after(0, self._update_title)

    def _upgrade_summary_part(self):
        """The title / cost-log PARAMETERS part naming the model the run
        started on and the call it left it at: "upgraded-from=<model>@call<N>"
        while an upgrade is active, else ""."""
        original = getattr(self, "_upgrade_original", None)
        if original is None:
            return ""
        return f"upgraded-from={original['model']}@call{original['call']}"

    # ── UI: the Agent Request dialog's Upgrade box ─────────────────────

    def _upgrade_box_state(self, target):
        """(enabled, ticked, label) for the dialog's box, from the run's state
        and the instruction's upgrade target."""
        if self._upgrade_active():
            level = self.thinking_mode.capitalize() if self.thinking_enabled else ""
            return (False, True,
                    f"Upgraded to {self._get_display_name(self.model)}"
                    f"{f' ({level})' if level else ''} for the rest of this run")
        if not target:
            return (False, False,
                    "Upgrade: no upgrade model set for this instruction "
                    "(Model Setup, on the main window)")
        if target["model"] == self.model:
            return (False, False,
                    f"Upgrade: this run is already on {self._get_display_name(self.model)}")
        level = self._upgrade_level_for(self.provider, target["model"], target.get("level"))
        return (True, False,
                f"Upgrade to {self._get_display_name(target['model'])}"
                f"{f' ({level})' if level else ''} for the rest of this run")

    def _upgrade_build_row(self, dlg):
        """The Upgrade checkbox for the caller to grid under the voice row.
        Returns (frame, checkbutton, var, target): `target` is what the box's
        label names, None when the box is disabled; the caller reads `var`
        when the reply is sent and hands the target to `_upgrade_apply` on
        the worker if it is set; the caller owns the mnemonics."""
        target = self._upgrade_target()
        enabled, ticked, label = self._upgrade_box_state(target)
        row = tk.Frame(dlg)
        var = tk.BooleanVar(master=dlg, value=ticked)
        box = tk.Checkbutton(row, text=label, variable=var, anchor="w",
                             state="normal" if enabled else "disabled")
        box.pack(side=tk.LEFT)
        return row, box, var, (target if enabled else None)

    # ── UI: Model Setup ────────────────────────────────────────────────

    def _upgrade_setup_from_main(self):
        """The main window's Model Setup button (bottom left, Alt+M)."""
        self._open_upgrade_setup(self.root)

    def _upgrade_setup_draft(self):
        """(shown, source) for Model Setup: the target the dialog opens on —
        {"provider", "model", "level"} for the live provider — and where it
        comes from: "own" (the instruction's), "default" (the provider's,
        the instruction has none of its own) or "none" (no upgrade)."""
        own = self._upgrade_own_target()
        if own is not None:
            return own, ("own" if own["model"] else "none")
        default = UPGRADE_DEFAULT_TARGETS.get(self.provider)
        if default:
            return {"provider": self.provider, **default}, "default"
        return {"provider": self.provider, "model": "", "level": ""}, "none"

    def _open_upgrade_setup(self, parent):
        """The modal dialog over `parent` that sets the upgrade model of the
        instruction whose settings are live (`_upgrade_instruction_name`) for
        its provider — the run's provider, so its model list is the one the
        app already holds (`available_models`) and nothing is fetched. Save
        writes the value through (`_upgrade_set_target`); Cancel keeps
        nothing."""
        existing = getattr(self, "_upgrade_setup_dialog", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass
        name = self._upgrade_instruction_name()
        provider = self.provider
        shown, source = self._upgrade_setup_draft()

        dlg = tk.Toplevel(parent)
        self._upgrade_setup_dialog = dlg
        dlg.withdraw()
        dlg.title(f"Model Setup — {name}" if name else "Model Setup")
        dlg.transient(parent)
        dlg.resizable(False, False)
        dlg.grid_columnconfigure(1, weight=1)
        font = ("Arial", 10)
        small = ("Arial", 9)

        def label(row, text):
            tk.Label(dlg, text=text, font=font, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(15, 8), pady=4)

        def value(row, text):
            tk.Label(dlg, text=text, font=font, anchor="w", justify="left",
                     wraplength=420).grid(row=row, column=1, sticky="w", padx=(0, 15), pady=4)

        label(0, "Instruction:")
        value(0, name or "(not saved yet: kept for this session until the editor's SAVE)")
        label(1, "Provider:")
        value(1, f"{provider} — this instruction runs on {self._get_display_name(self.model)}")

        label(2, "Upgrade model:")
        model_var = tk.StringVar()
        model_values = [UPGRADE_NO_MODEL_LABEL] + list(getattr(self, "available_models", None) or [])
        if shown["model"] and shown["model"] not in model_values:
            model_values.insert(1, shown["model"])     # saved, but off today's list
        model_combo = ttk.Combobox(dlg, textvariable=model_var, state="readonly",
                                   values=model_values, font=font, width=44)
        model_combo.grid(row=2, column=1, sticky="ew", padx=(0, 15), pady=4)

        label(3, "Thinking:")
        level_var = tk.StringVar()
        level_combo = ttk.Combobox(dlg, textvariable=level_var, state="readonly",
                                   font=font, width=44)
        level_combo.grid(row=3, column=1, sticky="ew", padx=(0, 15), pady=4)

        origin = {"own": "Saved with this instruction.",
                  "default": (f"This instruction has no upgrade model of its own yet: the "
                              f"{provider} default is shown. Save keeps it with the instruction."),
                  "none": "No upgrade: the Agent Request dialog's Upgrade box stays disabled."}
        def wrapping(text, fg):
            # A modest starting wraplength keeps the text from widening the
            # dialog's natural size; once laid out, it wraps at the full
            # width the dropdowns give the dialog (the voice status pattern).
            lbl = tk.Label(dlg, text=text, font=small, fg=fg, anchor="w", justify="left",
                           wraplength=440)
            lbl.bind("<Configure>", lambda e: lbl.config(wraplength=max(e.width - 4, 200)))
            return lbl

        origin_label = wrapping(origin[source], UPGRADE_MUTED_FG)
        origin_label.grid(row=4, column=0, columnspan=2, sticky="ew", padx=15, pady=(6, 0))
        note = wrapping(UPGRADE_NOTE, UPGRADE_MUTED_FG)
        note.grid(row=5, column=0, columnspan=2, sticky="ew", padx=15, pady=(6, 2))
        status = wrapping("", UPGRADE_ERROR_FG)
        status.grid(row=6, column=0, columnspan=2, sticky="ew", padx=15)

        def show_levels(saved_level):
            model = model_var.get()
            values = ([] if not model or model == UPGRADE_NO_MODEL_LABEL
                      else self._upgrade_level_values(provider, model))
            if not values:
                level_combo.config(values=[], state="disabled")
                level_var.set("")
                return
            level_combo.config(values=values, state="readonly")
            level_var.set(self._upgrade_level_for(provider, model, saved_level))

        def on_model(event=None):
            show_levels("")         # a fresh pick lands on the model's strongest level

        model_combo.bind("<<ComboboxSelected>>", on_model)
        model_var.set(shown["model"] or UPGRADE_NO_MODEL_LABEL)
        show_levels(shown["level"])

        def close(event=None):
            self._upgrade_setup_dialog = None
            dlg.destroy()
            return "break"

        def save():
            model = model_var.get()
            if model == UPGRADE_NO_MODEL_LABEL:
                model = ""
            try:
                self._upgrade_set_target({"provider": provider, "model": model,
                                          "level": level_var.get() if model else ""})
            except OSError as exc:
                status.config(text=f"Could not save the setting: {exc}")
                return
            close()

        btn_row = tk.Frame(dlg)
        btn_row.grid(row=7, column=0, columnspan=2, pady=(8, 12))
        save_btn = tk.Button(btn_row, text="Save", width=10, command=save)
        save_btn.pack(side=tk.LEFT, padx=8)
        cancel_btn = tk.Button(btn_row, text="Cancel", width=10, command=close)
        cancel_btn.pack(side=tk.LEFT, padx=8)

        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.bind("<Escape>", close)     # two small settings: no draft worth protecting
        bind_mnemonics(dlg, {"m": model_combo, "t": level_combo,
                             "s": save_btn, "c": cancel_btn})

        dlg.update_idletasks()
        placed = self._place_window(dlg, "model_setup",
                                    (dlg.winfo_reqwidth(), dlg.winfo_reqheight()), parent=parent)
        # Keep the position, give the size back to Tk: the notes re-wrap to
        # the dialog's width once it is laid out, and a size fixed now would
        # leave the height they give back as a blank strip under the buttons
        # (a position-only geometry does not cancel a size already set;
        # the empty one does).
        position = self._parse_geometry(placed)
        dlg.geometry("")
        if position:
            dlg.geometry(f"+{position[2]}+{position[3]}")
        dlg.deiconify()
        model_combo.focus_set()
        dlg.wait_visibility()
        dlg.grab_set()
        dlg.wait_window()
