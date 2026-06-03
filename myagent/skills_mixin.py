import os, sys, json, subprocess, tkinter as tk
from tkinter import messagebox

from myagent.constants import (IS_WINDOWS, _BASE_DIR, SKILLS_FILE, DEFAULT_SYSTEM_PROMPT, DEFAULT_GEOMETRY, _SUBPROCESS_NOWND, MONO_FONT)


class SkillsMixin:

    def _load_skills(self):
        if os.path.exists(SKILLS_FILE):
            try:
                with open(SKILLS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                migrated = False
                for name, sdata in data.items():
                    if "mode" not in sdata:
                        sdata["mode"] = "enabled" if sdata.pop("enabled", False) else "disabled"
                        migrated = True
                if migrated:
                    with open(SKILLS_FILE, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                return data
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_skills(self):
        with open(SKILLS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.skills, f, indent=2, ensure_ascii=False)

    def do_manage_skills(self, params):
        """CRUD operations on the shared skills library."""
        action = params.get("action", "")
        name = params.get("name", "")

        if action == "list":
            if not self.skills:
                return "No skills defined."
            lines = []
            for sn, sd in sorted(self.skills.items()):
                mode = sd.get("mode", "disabled")
                preview = sd.get("content", "")[:100].replace("\n", " ")
                lines.append(f"• {sn}  [{mode}]\n  {preview}...")
            return "\n".join(lines)

        if not name:
            return "Error: 'name' is required for this action."

        if action == "read":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            sd = self.skills[name]
            return json.dumps({"name": name, "content": sd.get("content", ""), "mode": sd.get("mode", "disabled")}, indent=2)

        elif action == "create":
            if name in self.skills:
                return f"Error: Skill '{name}' already exists. Use 'update' to modify it."
            content = params.get("content", "")
            if not content:
                return "Error: 'content' is required when creating a skill."
            mode = params.get("mode", "disabled")
            if mode not in ("disabled", "enabled", "on_demand"):
                return f"Error: Invalid mode '{mode}'. Valid modes: disabled, enabled, on_demand."
            self.skills[name] = {"content": content, "mode": mode}
            self._save_skills()
            self._post_skill_ui_refresh()
            return f"Skill '{name}' created successfully."

        elif action == "update":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found. Use 'create' to add it."
            content = params.get("content")
            mode = params.get("mode")
            if content is None and mode is None:
                return "Error: At least one of 'content' or 'mode' must be provided for update."
            if mode is not None and mode not in ("disabled", "enabled", "on_demand"):
                return f"Error: Invalid mode '{mode}'. Valid modes: disabled, enabled, on_demand."
            if content is not None:
                self.skills[name]["content"] = content
            if mode is not None:
                self.skills[name]["mode"] = mode
            self._save_skills()
            self._post_skill_ui_refresh()
            return f"Skill '{name}' updated successfully."

        elif action == "delete":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            del self.skills[name]
            self._save_skills()
            self._post_skill_ui_refresh()
            return f"Skill '{name}' deleted."

        return f"Error: Unknown action '{action}'."

    def do_run_instruction(self, params):
        """Launch a saved instruction as a separate MyAgent process."""
        name = params.get("name", "")
        headless = params.get("headless", True)

        if not name:
            return "Error: 'name' is required."

        # Verify instruction exists
        instructions = self._load_saved_instructions()
        if name not in instructions:
            available = ", ".join(sorted(instructions.keys())) if instructions else "(none)"
            return f"Error: Instruction '{name}' not found. Available: {available}"

        # Build command to launch a new MyAgent process
        script_path = os.path.join(_BASE_DIR, "MyAgent.py")
        cmd = [sys.executable, script_path, "-l", name]
        if headless:
            cmd.append("--headless")

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=_BASE_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            mode = "headless" if headless else "GUI"
            return f"Launched instruction '{name}' in {mode} mode (PID {proc.pid})."
        except Exception as e:
            return f"Error launching instruction '{name}': {e}"

    def _post_skill_ui_refresh(self):
        """Thread-safe refresh of Skills button and Skills Manager listbox."""
        def _refresh():
            self._update_skills_button()
            if (self.skills_editor_window and self.skills_editor_window.winfo_exists()
                    and self._skills_refresh_list):
                self._skills_refresh_list()
        self.root.after(0, _refresh)

    def _restore_skill_modes(self, entry):
        saved = entry.get("skill_modes", {})
        if not saved:
            return
        for sname in self.skills:
            mode = saved.get(sname, "disabled")  # new skills default to disabled
            if mode in ("disabled", "enabled", "on_demand"):
                self.skills[sname]["mode"] = mode
        self._save_skills()
        self._update_skills_button()
        # Refresh Skills Manager listbox if open
        if (self.skills_editor_window and self.skills_editor_window.winfo_exists()
                and self._skills_refresh_list):
            self._skills_refresh_list()

    def _update_skills_button(self):
        on_count = sum(1 for s in self.skills.values() if s.get("mode") == "enabled")
        od_count = sum(1 for s in self.skills.values() if s.get("mode") == "on_demand")
        if on_count and od_count:
            label = f"Skills ({on_count}+{od_count})"
        elif on_count:
            label = f"Skills ({on_count})"
        elif od_count:
            label = f"Skills (0+{od_count})"
        else:
            label = "Skills"
        try:
            self.skills_button.config(text=label)
        except (AttributeError, tk.TclError):
            pass  # Button doesn't exist yet or editor is closed

    def _update_ps_safety_button(self):
        n = len(self._disabled_confirm_patterns)
        base = "Safety"
        label = f"{base} ({n} bypassed)" if n else base
        try:
            self.ps_safety_button.config(text=label)
        except (AttributeError, tk.TclError):
            pass  # Button doesn't exist yet or editor is closed

    def _build_system_prompt(self):
        parts = [self.system_prompt]
        on_demand_names = []
        for name, skill in self.skills.items():
            if skill.get("mode") == "enabled":
                parts.append(f"## Skill: {name}\n{skill['content']}")
            elif skill.get("mode") == "on_demand":
                on_demand_names.append(name)
        if on_demand_names:
            listing = ", ".join(on_demand_names)
            parts.append(
                f"## On-Demand Skills\n"
                f"The following skills are available via the `get_skill` tool: {listing}\n"
                f"Call `get_skill` with the skill name when you need its content."
            )
        return "\n\n".join(parts)

    def _get_display_name(self, model_id):
        """Get display name for a model, provider-aware."""
        if self.provider == "OpenAI":
            return self._openai_model_display_names.get(model_id, model_id)
        if self.provider == "Gemini":
            return self._gemini_model_display_names.get(model_id, model_id)
        if self.provider == "Ollama":
            return getattr(self, "_ollama_model_display_names", {}).get(model_id, model_id)
        return self._model_display_names.get(model_id, model_id)

    def open_skills_editor(self):
        if self.skills_editor_window and self.skills_editor_window.winfo_exists():
            self.skills_editor_window.lift()
            return

        win = tk.Toplevel(self.root)
        win.withdraw()  # Hide until geometry is set
        win.title("Skills Manager")
        parent = (self.instruction_editor_window
                  if self.instruction_editor_window and self.instruction_editor_window.winfo_exists()
                  else self.root)
        if IS_WINDOWS:
            win.transient(parent)
        self.skills_editor_window = win

        def _on_skills_close():
            self._last_skills_dialog_geometry = win.geometry()
            self._save_last_state()
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_skills_close)

        top = tk.Frame(win)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 5))

        tk.Label(top, text="Skill Name", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        name_entry = tk.Entry(top, font=("Arial", 10), width=20)
        name_entry.pack(side=tk.LEFT, padx=(0, 5))

        def save_skill():
            name = name_entry.get().strip()
            if not name:
                messagebox.showwarning("No name", "Enter a name for the skill.", parent=win)
                return
            content = text_editor.get("1.0", "end-1c").strip()
            if not content:
                messagebox.showwarning("Empty", "The skill content is empty.", parent=win)
                return
            mode = self.skills.get(name, {}).get("mode", "disabled")
            self.skills[name] = {"content": content, "mode": mode}
            self._save_skills()
            refresh_list()
            self._update_skills_button()

        def delete_skill():
            sel = skill_listbox.curselection()
            if not sel:
                messagebox.showwarning("No selection", "Select a skill to delete.", parent=win)
                return
            name = skill_listbox.get(sel[0])[5:]
            if name in self.skills:
                del self.skills[name]
                self._save_skills()
                refresh_list()
                name_entry.delete(0, tk.END)
                text_editor.delete("1.0", tk.END)
                self._update_skills_button()

        def new_skill():
            name_entry.delete(0, tk.END)
            text_editor.delete("1.0", tk.END)
            skill_listbox.selection_clear(0, tk.END)

        tk.Button(top, text="SAVE", command=save_skill, width=6).pack(side=tk.LEFT, padx=(5, 2))
        tk.Button(top, text="DELETE", command=delete_skill, width=7).pack(side=tk.LEFT, padx=(2, 2))
        tk.Button(top, text="NEW", command=new_skill, width=5).pack(side=tk.LEFT, padx=(2, 0))

        left = tk.Frame(win)
        left.grid(row=1, column=0, sticky="nsew", padx=(10, 5), pady=(0, 10))
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        skill_listbox = tk.Listbox(left, font=("Arial", 10), width=20)
        skill_listbox.grid(row=0, column=0, sticky="nsew")
        list_scrollbar = tk.Scrollbar(left, command=skill_listbox.yview)
        list_scrollbar.grid(row=0, column=1, sticky="ns")
        skill_listbox.config(yscrollcommand=list_scrollbar.set)

        toggle_btn = tk.Button(left, text="Cycle Mode", font=("Arial", 9))
        toggle_btn.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))

        def refresh_list():
            skill_listbox.delete(0, tk.END)
            for sname, sdata in self.skills.items():
                mode = sdata.get("mode", "disabled")
                if mode == "enabled":
                    prefix = "[ON] "
                elif mode == "on_demand":
                    prefix = "[OD] "
                else:
                    prefix = "     "
                skill_listbox.insert(tk.END, f"{prefix}{sname}")
            for i, (sname, sdata) in enumerate(self.skills.items()):
                mode = sdata.get("mode", "disabled")
                if mode == "enabled":
                    skill_listbox.itemconfig(i, fg="#2e7d32")
                elif mode == "on_demand":
                    skill_listbox.itemconfig(i, fg="#1565c0")

        self._skills_refresh_list = refresh_list

        def on_select(event):
            sel = skill_listbox.curselection()
            if not sel:
                return
            name = skill_listbox.get(sel[0])[5:]
            if name in self.skills:
                name_entry.delete(0, tk.END)
                name_entry.insert(0, name)
                text_editor.delete("1.0", tk.END)
                text_editor.insert("1.0", self.skills[name]["content"])

        def toggle_skill():
            sel = skill_listbox.curselection()
            if not sel:
                messagebox.showwarning("No selection", "Select a skill to toggle.", parent=win)
                return
            name = skill_listbox.get(sel[0])[5:]
            if name in self.skills:
                cycle = {"disabled": "enabled", "enabled": "on_demand", "on_demand": "disabled"}
                cur = self.skills[name].get("mode", "disabled")
                self.skills[name]["mode"] = cycle.get(cur, "disabled")
                self._save_skills()
                idx = sel[0]
                refresh_list()
                skill_listbox.selection_set(idx)
                skill_listbox.see(idx)
                self._update_skills_button()

        skill_listbox.bind("<<ListboxSelect>>", on_select)
        toggle_btn.config(command=toggle_skill)

        right = tk.Frame(win)
        right.grid(row=1, column=1, sticky="nsew", padx=(5, 10), pady=(0, 10))
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        text_editor = tk.Text(right, wrap=tk.WORD, font=(MONO_FONT, 10))
        text_editor.grid(row=0, column=0, sticky="nsew")
        text_scrollbar = tk.Scrollbar(right, command=text_editor.yview)
        text_scrollbar.grid(row=0, column=1, sticky="ns")
        text_editor.config(yscrollcommand=text_scrollbar.set)

        win.grid_columnconfigure(0, weight=0)
        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(1, weight=1)

        refresh_list()

        # Restore geometry AFTER all content is laid out, then show
        win.update_idletasks()
        saved_geo = getattr(self, '_last_skills_dialog_geometry', None)
        if saved_geo:
            win.geometry(self._sanitize_geometry(saved_geo, min_w=400, min_h=300))
        else:
            win.geometry("750x500")
        win.deiconify()
