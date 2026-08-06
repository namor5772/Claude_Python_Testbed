import os, re, sys, json, subprocess, tempfile, threading, time, tkinter as tk
from tkinter import messagebox

from myagent.constants import (IS_WINDOWS, _BASE_DIR, SKILLS_DIR, MONO_FONT,
                               COMMAND_CONFIRM, GMAIL_CONFIRM_TOOLS,
                               PROTON_CONFIRM_TOOLS, OUTLOOK_CONFIRM_TOOLS)
from myagent.datapaths import delete_skill_tree_entry, load_skills_tree, save_skills_tree

# Serializes do_run_instruction's shared-state prologue (store load, which may
# absorb OneDrive conflict forks on disk, + the spawn itself) so several
# run_instruction calls in one assistant turn — the parallel fan-out pattern —
# can't interleave those mutations. The waited poll loop runs OUTSIDE the lock,
# so concurrent waited children genuinely overlap.
_SPAWN_LOCK = threading.Lock()


class SkillsMixin:

    def _load_skills(self):
        # Per-skill SKILL.md tree (frontmatter: name/description/mode). Runs
        # the one-shot skills.json→tree migration and heals OneDrive per-file
        # conflict forks; every entry comes back with a valid mode.
        return load_skills_tree(SKILLS_DIR)

    def _save_skills(self):
        # Diff-aware and WRITE-ONLY (never deletes folders) — deletion is an
        # explicit action via delete_skill_tree_entry at the delete callsites.
        save_skills_tree(SKILLS_DIR, self.skills)

    @staticmethod
    def _desc_length_warning(desc):
        """Soft Agent-Skills-spec guideline (<=1024 chars) — warn, never reject."""
        if len(desc) > 1024:
            return (f"\nWarning: description is {len(desc)} chars; the Agent Skills "
                    "guideline is <=1024. Consider shortening it.")
        return ""

    @staticmethod
    def _name_convention_warning(name):
        """Soft Agent-Skills-spec naming guideline (lowercase letters/digits/
        hyphens, <=64 chars, e.g. 'westpac-login') — warn, never reject, so
        legacy Title-Case names keep working."""
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
            return (f"\nWarning: name '{name}' doesn't follow the Agent Skills "
                    "naming convention (lowercase letters/digits/hyphens, max 64 "
                    "chars, e.g. 'westpac-login').")
        return ""

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
                desc = (sd.get("description") or "").strip()
                info = desc or sd.get("content", "")[:100].replace("\n", " ") + "..."
                lines.append(f"• {sn}  [{mode}]\n  {info}")
            return "\n".join(lines)

        if not name:
            return "Error: 'name' is required for this action."

        if action == "read":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            sd = self.skills[name]
            return json.dumps({"name": name, "content": sd.get("content", ""),
                               "mode": sd.get("mode", "disabled"),
                               "description": sd.get("description", "")}, indent=2)

        if action == "create":
            if name in self.skills:
                return f"Error: Skill '{name}' already exists. Use 'update' to modify it."
            content = params.get("content", "")
            if not content:
                return "Error: 'content' is required when creating a skill."
            mode = params.get("mode", "disabled")
            if mode not in ("disabled", "enabled", "on_demand"):
                return f"Error: Invalid mode '{mode}'. Valid modes: disabled, enabled, on_demand."
            entry = {"content": content, "mode": mode}
            desc = (params.get("description") or "").strip()
            if desc:
                entry["description"] = desc
            self.skills[name] = entry
            self._save_skills()
            self._post_skill_ui_refresh()
            return (f"Skill '{name}' created successfully."
                    + self._desc_length_warning(desc)
                    + self._name_convention_warning(name))

        if action == "update":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found. Use 'create' to add it."
            content = params.get("content")
            mode = params.get("mode")
            description = params.get("description")
            if content is None and mode is None and description is None:
                return ("Error: At least one of 'content', 'mode' or 'description' "
                        "must be provided for update.")
            if mode is not None and mode not in ("disabled", "enabled", "on_demand"):
                return f"Error: Invalid mode '{mode}'. Valid modes: disabled, enabled, on_demand."
            if content is not None:
                self.skills[name]["content"] = content
            if mode is not None:
                self.skills[name]["mode"] = mode
            desc = ""
            if description is not None:
                desc = description.strip()
                if desc:
                    self.skills[name]["description"] = desc
                else:
                    self.skills[name].pop("description", None)  # "" clears it
            self._save_skills()
            self._post_skill_ui_refresh()
            return f"Skill '{name}' updated successfully." + self._desc_length_warning(desc)

        if action == "delete":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            del self.skills[name]
            delete_skill_tree_entry(SKILLS_DIR, name)  # _save_skills never deletes
            self._save_skills()
            self._post_skill_ui_refresh()
            return f"Skill '{name}' deleted."

        return f"Error: Unknown action '{action}'."

    def do_run_instruction(self, params):
        """Launch a saved instruction as a separate MyAgent process.

        Fire-and-forget by default; with wait=true this blocks until the child
        exits, then returns the child's final report (read from a --result-file
        the child writes when its loop ends) — the subagent-result pattern, so
        a parent agent can consume a child's answer instead of merely spawning
        it. PARALLEL_SAFE: when the model issues several run_instruction calls
        in one assistant turn, stream_worker's executor runs them concurrently,
        so waited children overlap (parallel fan-out) — each blocks only its
        own worker thread. Only the prologue below is serialized (_SPAWN_LOCK);
        children can't collide on instance slots because the lock-file claim is
        O_EXCL-atomic (state_mixin._claim_instance_number)."""
        name = params.get("name", "")
        headless = params.get("headless", True)
        wait = bool(params.get("wait", False))
        extra_text = params.get("extra_text") or ""
        try:
            timeout_s = int(params.get("timeout_seconds") or 600)
        except (TypeError, ValueError):
            timeout_s = 600

        if not name:
            return "Error: 'name' is required."

        # Prologue under _SPAWN_LOCK: concurrent fan-out calls must not
        # interleave the store load (fork absorption mutates disk) or spawn.
        with _SPAWN_LOCK:
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

            result_path = None
            if wait:
                fd, result_path = tempfile.mkstemp(prefix="myagent_result_", suffix=".json")
                os.close(fd)
                cmd += ["--result-file", result_path]

            # Per-spawn task addendum travels by temp FILE, not argv or env: argv
            # chokes on newlines/length on Windows, and an inherited env var would
            # leak this parent's extra_text into any grandchild the child spawns.
            # The parent deletes it after a waited child exits; a fire-and-forget
            # child's file stays in %TEMP% (the child must still be able to read it).
            extra_path = None
            if extra_text:
                fd, extra_path = tempfile.mkstemp(prefix="myagent_extra_", suffix=".txt")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(extra_text)
                cmd += ["--extra-file", extra_path]

            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=_BASE_DIR,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as e:
                for p in (result_path, extra_path):
                    if p:
                        try:
                            os.remove(p)
                        except OSError:
                            pass
                return f"Error launching instruction '{name}': {e}"

        if not wait:
            mode = "headless" if headless else "GUI"
            noted = " with extra task context" if extra_text else ""
            return f"Launched instruction '{name}' in {mode} mode{noted} (PID {proc.pid})."

        # Blocking wait: poll so STOP stays responsive and the timeout is
        # enforced. Child-process exit is the synchronization point — the
        # result file is fully written before the headless close runs.
        start = time.time()
        try:
            while proc.poll() is None:
                if self.stop_requested:
                    proc.terminate()
                    return (f"run_instruction '{name}' cancelled: STOP pressed "
                            "while waiting; child process terminated.")
                if time.time() - start > timeout_s:
                    proc.terminate()
                    return (f"Error: instruction '{name}' did not finish within "
                            f"{timeout_s}s; child process terminated. Its partial "
                            "transcript (if the instruction names a chat) is in "
                            "saved_chats/.")
                time.sleep(1)
            elapsed = int(time.time() - start)
            try:
                with open(result_path, encoding="utf-8") as f:
                    result = json.load(f)
            except (OSError, ValueError):
                return (f"Error: instruction '{name}' exited (code {proc.returncode}, "
                        f"{elapsed}s) but wrote no readable result — it may have "
                        "failed before its agent loop started.")
            status = result.get("status", "unknown")
            final_text = (result.get("final_text") or "").strip()
            if len(final_text) > 20000:
                final_text = final_text[:20000] + "\n…[report truncated at 20000 chars]"
            out = f"Instruction '{name}' finished (status={status}, {elapsed}s)."
            if result.get("error"):
                out += f"\nChild error: {result['error']}"
            out += f"\nFinal report:\n{final_text}" if final_text else "\n(No final text.)"
            return out
        finally:
            for p in (result_path, extra_path):
                if p:
                    try:
                        os.remove(p)
                    except OSError:
                        pass

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
        # Session-only: applying an instruction's saved skill modes updates the
        # live session (self.skills drives _build_system_prompt) but is NOT
        # persisted to skills.json. skills.json is the sticky global store, changed
        # only by explicit Skills Manager / manage_skills edits — otherwise loading
        # an instruction (or restoring the last applied state on launch) would
        # silently overwrite the user's global skill modes.
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
        # Cross-platform instructions carry a UNION of bypass patterns (e.g.
        # \bRemove-Item\b alongside \brm\b); only those in this OS's
        # COMMAND_CONFIRM (or the OS-independent mail tool names) have any
        # effect — or a checkbox in the Safety dialog. Count the rest
        # separately so the label matches what the dialog shows.
        relevant = (set(COMMAND_CONFIRM) | set(GMAIL_CONFIRM_TOOLS)
                    | set(PROTON_CONFIRM_TOOLS) | set(OUTLOOK_CONFIRM_TOOLS))
        n = len(self._disabled_confirm_patterns & relevant)
        foreign = len(self._disabled_confirm_patterns) - n
        base = "Safety"
        if n and foreign:
            label = f"{base} ({n} bypassed, {foreign} other-OS)"
        elif n:
            label = f"{base} ({n} bypassed)"
        elif foreign:
            label = f"{base} ({foreign} other-OS)"
        else:
            label = base
        try:
            self.ps_safety_button.config(text=label)
        except (AttributeError, tk.TclError):
            pass  # Button doesn't exist yet or editor is closed

    @staticmethod
    def _format_on_demand_listing(skills):
        """Build the '## On-Demand Skills' system-prompt block: one bullet per
        on_demand skill carrying its description — the what-it-does / when-to-
        use-it routing signal, Agent-Skills style. A skill without a description
        is listed by bare name. Returns "" when nothing is on_demand."""
        lines = []
        for name, skill in skills.items():
            if skill.get("mode") != "on_demand":
                continue
            desc = (skill.get("description") or "").strip()
            lines.append(f"- {name} — {desc}" if desc else f"- {name}")
        if not lines:
            return ""
        return (
            "## On-Demand Skills\n"
            "The following skills are available via the `get_skill` tool. "
            "Call `get_skill` with the skill name when its description matches "
            "the task at hand:\n" + "\n".join(lines)
        )

    def _build_system_prompt(self):
        parts = [self.system_prompt]
        for name, skill in self.skills.items():
            if skill.get("mode") == "enabled":
                parts.append(f"## Skill: {name}\n{skill['content']}")
        od_block = self._format_on_demand_listing(self.skills)
        if od_block:
            parts.append(od_block)
        return "\n\n".join(parts)

    def _get_display_name(self, model_id):
        """Get display name for a model, provider-aware."""
        if self.provider == "OpenAI":
            return self._openai_model_display_names.get(model_id, model_id)
        if self.provider == "Google":
            return self._gemini_model_display_names.get(model_id, model_id)
        if self.provider == "xAI":
            return getattr(self, "_xai_model_display_names", {}).get(model_id, model_id)
        if self.provider == "Moonshot":
            return getattr(self, "_kimi_model_display_names", {}).get(model_id, model_id)
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
        name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        def save_skill():
            name = name_entry.get().strip()
            if not name:
                messagebox.showwarning("No name", "Enter a name for the skill.", parent=win)
                return
            content = text_editor.get("1.0", "end-1c").strip()
            if not content:
                messagebox.showwarning("Empty", "The skill content is empty.", parent=win)
                return
            # Merge into the existing entry (never whole-entry replace) so fields
            # this editor doesn't show — e.g. one added by a newer version on
            # another machine — survive a SAVE here.
            entry = dict(self.skills.get(name, {}))
            entry["content"] = content
            entry.setdefault("mode", "disabled")
            desc = desc_entry.get().strip()
            if desc:
                entry["description"] = desc
            else:
                entry.pop("description", None)
            self.skills[name] = entry
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
                delete_skill_tree_entry(SKILLS_DIR, name)  # _save_skills never deletes
                self._save_skills()
                refresh_list()
                name_entry.delete(0, tk.END)
                desc_entry.delete(0, tk.END)
                text_editor.delete("1.0", tk.END)
                self._update_skills_button()

        def new_skill():
            name_entry.delete(0, tk.END)
            desc_entry.delete(0, tk.END)
            text_editor.delete("1.0", tk.END)
            skill_listbox.selection_clear(0, tk.END)

        # Packed side=RIGHT in reverse order so the visual left-to-right order
        # stays SAVE, DELETE, NEW while the buttons hug the right edge. The
        # name_entry above (fill=X, expand) absorbs all the space in between.
        tk.Button(top, text="NEW", command=new_skill, width=5).pack(side=tk.RIGHT, padx=2)
        tk.Button(top, text="DELETE", command=delete_skill, width=7).pack(side=tk.RIGHT, padx=2)
        tk.Button(top, text="SAVE", command=save_skill, width=6).pack(side=tk.RIGHT, padx=2)

        # Description row: the what+when trigger signal shown in the system
        # prompt for on_demand skills (Agent-Skills style). Optional.
        desc_row = tk.Frame(win)
        desc_row.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 5))
        tk.Label(desc_row, text="Description", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        desc_entry = tk.Entry(desc_row, font=("Arial", 10))
        desc_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        left = tk.Frame(win)
        left.grid(row=2, column=0, sticky="nsew", padx=(10, 5), pady=(0, 10))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        # Cycle Mode sits ABOVE the list, sized to its label and left-aligned
        toggle_btn = tk.Button(left, text="Cycle Mode", font=("Arial", 9))
        toggle_btn.grid(row=0, column=0, sticky="w", pady=(0, 5))

        skill_listbox = tk.Listbox(left, font=("Arial", 10), width=40)
        skill_listbox.grid(row=1, column=0, sticky="nsew")
        list_scrollbar = tk.Scrollbar(left, command=skill_listbox.yview)
        list_scrollbar.grid(row=1, column=1, sticky="ns")
        skill_listbox.config(yscrollcommand=list_scrollbar.set)

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
            for i, sdata in enumerate(self.skills.values()):
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
                desc_entry.delete(0, tk.END)
                desc_entry.insert(0, self.skills[name].get("description", ""))
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

        def _cycle_on_space(event):
            # Space bar mirrors the Cycle Mode button on the selected skill.
            toggle_skill()
            return "break"  # suppress Tk's default <space> select-active binding

        skill_listbox.bind("<<ListboxSelect>>", on_select)
        skill_listbox.bind("<space>", _cycle_on_space)
        toggle_btn.config(command=toggle_skill)

        right = tk.Frame(win)
        right.grid(row=2, column=1, sticky="nsew", padx=(5, 10), pady=(0, 10))
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        text_editor = tk.Text(right, wrap=tk.WORD, font=(MONO_FONT, 10))
        text_editor.grid(row=0, column=0, sticky="nsew")
        text_scrollbar = tk.Scrollbar(right, command=text_editor.yview)
        text_scrollbar.grid(row=0, column=1, sticky="ns")
        text_editor.config(yscrollcommand=text_scrollbar.set)

        win.grid_columnconfigure(0, weight=0)
        win.grid_columnconfigure(1, weight=1)
        win.grid_rowconfigure(2, weight=1)

        refresh_list()

        # Restore geometry AFTER all content is laid out, then show
        win.update_idletasks()
        saved_geo = getattr(self, '_last_skills_dialog_geometry', None)
        if saved_geo:
            win.geometry(self._sanitize_geometry(saved_geo, min_w=400, min_h=300))
        else:
            win.geometry("900x500")
        win.deiconify()
