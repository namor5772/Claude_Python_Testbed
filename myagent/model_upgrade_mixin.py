"""Model upgrade for the rest of a run (mixin #26, 2026-09-27).

A run started on a cheap model — Sonnet 5 following a set of skills, say —
ends at the Agent Request dialog, where the user typically asks the agent to
look back over the run just had and tighten the skills it used. That is the
task a stronger model does better, so the dialog carries an **Upgrade** box:
ticked, the reply is sent as usual and every API call FROM THAT REPLY ON goes
to the upgrade model with its own thinking level; the conversation, the tools,
the system prompt and the run's other settings (temperature, verbosity, Fast)
are untouched. The upgrade lasts for the rest of the run; it never outlives it
and never reaches the applied instruction, the state file or the store.

The upgrade model is chosen per provider in **Model Setup**, a button beside
Voice Setup at the bottom left of the main window: a run upgrades within its
own provider only — the history's provider-specific reasoning artefacts stay
readable there (live-probed 2026-09-27: a Sonnet 5 thinking block replayed to
Fable 5.1 or Opus 5, a Gemini 3.8 Flash thought signature replayed to 3.1
Pro, a gpt-5.6-terra function call replayed to gpt-6-astra — all HTTP 200
with no history edit; stripping a Gemini signature is the one thing that
FAILS, so nothing is ever stripped). The settings are the user's and the
machine's, like Voice Setup's: ~/.config/myagent-upgrade/config.json.

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
never on the live app, which may be mid-call on another provider.
"""

import json
import os
import queue
import tempfile
import threading
import tkinter as tk
from tkinter import ttk

from myagent.constants import (
    BUDGET_PRESETS, PROVIDERS, UPGRADE_DEFAULT_TARGETS, UPGRADE_NO_MODEL_LABEL,
)
from myagent.keyboard import bind_mnemonics

UPGRADE_CONFIG_DIR = os.path.expanduser("~/.config/myagent-upgrade")
UPGRADE_CONFIG_FILE = os.path.join(UPGRADE_CONFIG_DIR, "config.json")

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

    # ── Settings (pure: no Tk, no network) ──────────────────────────────

    @staticmethod
    def _upgrade_sanitize_config(raw):
        """Any JSON value → {"targets": {provider: {"model", "level"}}} holding
        only well-formed targets for known providers. A provider absent here
        has NO upgrade (the dialog's box is then disabled for its runs)."""
        raw = raw if isinstance(raw, dict) else {}
        saved = raw.get("targets")
        targets = {}
        if isinstance(saved, dict):
            for provider in PROVIDERS:
                entry = saved.get(provider)
                if not isinstance(entry, dict):
                    continue
                model = entry.get("model")
                if not isinstance(model, str) or not model.strip():
                    continue
                level = entry.get("level")
                level = level.strip() if isinstance(level, str) else ""
                targets[provider] = {"model": model.strip(), "level": level}
        return {"targets": targets}

    @staticmethod
    def _upgrade_load_config(path=None):
        """The saved targets; the curated defaults (UPGRADE_DEFAULT_TARGETS)
        only when there is no file yet — a saved file that names no target
        for a provider means the user cleared it."""
        try:
            with open(path or UPGRADE_CONFIG_FILE, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            return ModelUpgradeMixin._upgrade_sanitize_config(
                {"targets": UPGRADE_DEFAULT_TARGETS})
        return ModelUpgradeMixin._upgrade_sanitize_config(raw)

    @staticmethod
    def _upgrade_save_config(cfg, path=None):
        """Atomic write (mkstemp + os.replace beside the target), as the voice
        settings: two instances may save at once."""
        path = path or UPGRADE_CONFIG_FILE
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix="config_", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(ModelUpgradeMixin._upgrade_sanitize_config(cfg), f, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def _upgrade_target(self, provider=None):
        """The saved upgrade for `provider` (the run's by default):
        {"model", "level"} or None."""
        cfg = self._upgrade_load_config()
        return cfg["targets"].get(provider or self.provider)

    # ── Levels: what a model offers, and what a level means ───────────

    def _upgrade_probe(self, provider, model):
        """A bare instance of this app's class carrying only what the
        per-family detection helpers read — provider, model, the capability
        tables the live fetches filled — so a model of ANY provider can be
        asked what it supports without touching the live app (which may be
        mid-call on a different provider on the worker thread)."""
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
        """The thinking levels the Model Setup dialog offers for a model — the
        rungs the instruction editor's own controls would show for it, with
        an "Off" (or the boolean "On") where the editor uses a checkbox, and
        [] for a model with no thinking control at all. Lower-cased, each is
        what `_upgrade_params_for` reads."""
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
        root = getattr(self, "root", None)
        if root is not None:
            root.after(0, self._update_title)
        return True

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
        and the provider's saved target."""
        if self._upgrade_active():
            level = self.thinking_mode.capitalize() if self.thinking_enabled else ""
            return (False, True,
                    f"Upgraded to {self._get_display_name(self.model)}"
                    f"{f' ({level})' if level else ''} for the rest of this run")
        if not target:
            return (False, False,
                    f"Upgrade: no upgrade model set for {self.provider} "
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
        Returns (frame, checkbutton, var, target): the caller reads `var`
        when the reply is sent and calls `_upgrade_apply(target)` on the
        worker if it is set; the caller owns the mnemonics."""
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

    def _upgrade_providers_available(self):
        """The providers a run can be on here — a key set, or Ollama up."""
        flags = {"Anthropic": "_has_anthropic", "OpenAI": "_has_openai",
                 "Google": "_has_gemini", "xAI": "_has_xai",
                 "Moonshot": "_has_kimi", "Ollama": "_has_ollama"}
        return [p for p in PROVIDERS if getattr(self, flags[p], False)]

    def _upgrade_model_cache(self):
        """provider → the model list fetched this session for the dialog.
        The run's own provider needs no fetch: `available_models` is it."""
        cache = getattr(self, "_upgrade_models_fetched", None)
        if cache is None:
            cache = self._upgrade_models_fetched = {}
        return cache

    def _upgrade_fetch_models(self, provider):
        """The live model list for one provider (worker thread: network
        only) — the same fetchers the editor's picker uses. NOT called for
        the run's own provider (its fetch resets capability tables the live
        run reads)."""
        fetch = {"OpenAI": "_fetch_openai_models", "Google": "_fetch_gemini_models",
                 "xAI": "_fetch_xai_models", "Moonshot": "_fetch_kimi_models",
                 "Ollama": "_fetch_ollama_models"}.get(provider, "_fetch_available_models")
        return list(getattr(self, fetch)())

    def _open_upgrade_setup(self, parent):
        """The modal settings dialog over `parent`: one upgrade model + level
        per provider. Save writes ~/.config/myagent-upgrade/config.json, read
        afresh by every Agent Request dialog — in this instance and every
        other, whatever instruction is applied."""
        existing = getattr(self, "_upgrade_setup_dialog", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass
        cfg = self._upgrade_load_config()
        targets = {p: dict(t) for p, t in cfg["targets"].items()}   # the draft
        providers = self._upgrade_providers_available() or [self.provider]
        cache = self._upgrade_model_cache()
        cache.setdefault(self.provider, list(getattr(self, "available_models", None) or []))

        dlg = tk.Toplevel(parent)
        self._upgrade_setup_dialog = dlg
        dlg.withdraw()
        dlg.title("Model Setup")
        dlg.transient(parent)
        dlg.resizable(False, False)
        dlg.grid_columnconfigure(1, weight=1)
        font = ("Arial", 10)

        def label(row, text):
            tk.Label(dlg, text=text, font=font, anchor="w").grid(
                row=row, column=0, sticky="w", padx=(15, 8), pady=4)

        label(0, "Provider:")
        shown = [self.provider if self.provider in providers else providers[0]]
        provider_var = tk.StringVar(value=shown[0])
        provider_combo = ttk.Combobox(dlg, textvariable=provider_var, state="readonly",
                                      values=providers, font=font, width=44)
        provider_combo.grid(row=0, column=1, sticky="ew", padx=(0, 15), pady=4)

        label(1, "Upgrade model:")
        model_var = tk.StringVar()
        model_combo = ttk.Combobox(dlg, textvariable=model_var, state="readonly",
                                   font=font, width=44)
        model_combo.grid(row=1, column=1, sticky="ew", padx=(0, 15), pady=4)

        label(2, "Thinking:")
        level_var = tk.StringVar()
        level_combo = ttk.Combobox(dlg, textvariable=level_var, state="readonly",
                                   font=font, width=44)
        level_combo.grid(row=2, column=1, sticky="ew", padx=(0, 15), pady=4)

        note = tk.Label(dlg, text=UPGRADE_NOTE, font=("Arial", 9), fg=UPGRADE_MUTED_FG,
                        anchor="w", justify="left", wraplength=440)
        note.grid(row=3, column=0, columnspan=2, sticky="ew", padx=15, pady=(6, 2))
        status = tk.Label(dlg, text="", font=("Arial", 9), fg=UPGRADE_ERROR_FG,
                          anchor="w", justify="left", wraplength=440)
        status.grid(row=4, column=0, columnspan=2, sticky="ew", padx=15)

        def keep_draft():
            """The shown provider's fields → the draft."""
            provider = shown[0]
            model = model_var.get()
            if not model or model == UPGRADE_NO_MODEL_LABEL:
                targets.pop(provider, None)
            else:
                targets[provider] = {"model": model, "level": level_var.get()}

        def show_levels(event=None):
            model = model_var.get()
            if not model or model == UPGRADE_NO_MODEL_LABEL:
                level_combo.config(values=[], state="disabled")
                level_var.set("")
                return
            values = self._upgrade_level_values(shown[0], model)
            if not values:
                level_combo.config(values=[], state="disabled")
                level_var.set("")
                return
            level_combo.config(values=values, state="readonly")
            saved = targets.get(shown[0], {}).get("level") if \
                targets.get(shown[0], {}).get("model") == model else level_var.get()
            level_var.set(self._upgrade_level_for(shown[0], model, saved))

        def show_models(provider):
            values = [UPGRADE_NO_MODEL_LABEL] + list(cache.get(provider) or [])
            target = targets.get(provider)
            if target and target["model"] not in values:
                values.insert(1, target["model"])      # saved, but off today's list
            model_combo.config(values=values)
            model_var.set(target["model"] if target else UPGRADE_NO_MODEL_LABEL)
            show_levels()

        def on_model(event=None):
            level_var.set("")       # a fresh pick takes the model's strongest level
            show_levels()

        model_combo.bind("<<ComboboxSelected>>", on_model)
        level_combo.bind("<<ComboboxSelected>>", lambda e: keep_draft())

        def on_provider(event=None):
            keep_draft()
            shown[0] = provider_var.get()
            show_models(shown[0])

        provider_combo.bind("<<ComboboxSelected>>", on_provider)
        show_models(shown[0])

        def close(event=None):
            self._upgrade_setup_dialog = None
            dlg.destroy()
            return "break"

        def save():
            keep_draft()
            try:
                self._upgrade_save_config({"targets": targets})
            except OSError as exc:
                status.config(text=f"Could not save the settings: {exc}")
                return
            close()

        btn_row = tk.Frame(dlg)
        btn_row.grid(row=5, column=0, columnspan=2, pady=(8, 12))
        save_btn = tk.Button(btn_row, text="Save", width=10, command=save)
        save_btn.pack(side=tk.LEFT, padx=8)
        cancel_btn = tk.Button(btn_row, text="Cancel", width=10, command=close)
        cancel_btn.pack(side=tk.LEFT, padx=8)

        dlg.protocol("WM_DELETE_WINDOW", close)
        dlg.bind("<Escape>", close)     # three small settings: no draft worth protecting
        bind_mnemonics(dlg, {"p": provider_combo, "m": model_combo, "t": level_combo,
                             "s": save_btn, "c": cancel_btn})

        # The other providers' live model lists are network calls: a worker
        # fetches, the Tk thread drains; fetched once per session.
        fetched = queue.Queue()
        missing = [p for p in providers if p not in cache]

        def fetch():
            for provider in missing:
                try:
                    fetched.put((provider, self._upgrade_fetch_models(provider)))
                except Exception:
                    pass
            fetched.put(None)

        def drain():
            try:
                if not dlg.winfo_exists():
                    return
                while True:
                    item = fetched.get_nowait()
                    if item is None:
                        return
                    provider, values = item
                    cache[provider] = values
                    if provider == shown[0]:
                        keep_draft()
                        show_models(provider)
            except queue.Empty:
                dlg.after(100, drain)
            except tk.TclError:
                pass

        if missing:
            threading.Thread(target=fetch, daemon=True).start()
            dlg.after(100, drain)

        dlg.update_idletasks()
        placed = self._place_window(dlg, "model_setup",
                                    (dlg.winfo_reqwidth(), dlg.winfo_reqheight()), parent=parent)
        position = self._parse_geometry(placed)
        if position:
            dlg.geometry(f"+{position[2]}+{position[3]}")
        dlg.deiconify()
        provider_combo.focus_set()
        dlg.wait_visibility()
        dlg.grab_set()
        dlg.wait_window()
