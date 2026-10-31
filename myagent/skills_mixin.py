import os
import re
import sys
import json
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox

from myagent.constants import (IS_WINDOWS, _BASE_DIR, SKILLS_DIR, CHATS_DIR, MONO_FONT,
                               COMMAND_CONFIRM, GMAIL_CONFIRM_TOOLS,
                               PROTON_CONFIRM_TOOLS, OUTLOOK_CONFIRM_TOOLS)
from myagent.datapaths import (copy_skill_resources, delete_skill_tree_entry,
                               list_skill_resources, load_skills_tree,
                               save_skills_tree, skill_dir_for,
                               skill_resource_path)
from myagent.instruction_layout import file_page, normalize_section, renumber, rows, section_of
from myagent.keyboard import bind_mnemonics
from myagent.sectioned_list import SectionedList, theme_colors
from myagent.ui_mixin import SMALL_BUTTON_FONT
from myagent.pane_notes import PaneNotesMixin

# Serializes do_run_instruction's shared-state prologue (store load, which may
# absorb OneDrive conflict forks on disk, + the spawn itself) so several
# run_instruction calls in one assistant turn — the parallel fan-out pattern —
# can't interleave those mutations. The waited poll loop runs OUTSIDE the lock,
# so concurrent waited children genuinely overlap.
_SPAWN_LOCK = threading.Lock()


class SkillsMixin(PaneNotesMixin):

    def _load_skills(self):
        # Per-skill SKILL.md tree (frontmatter: name/description/mode). Runs
        # the one-shot skills.json→tree migration and heals OneDrive per-file
        # conflict forks; every entry comes back with a valid mode. The modes
        # read here are the GLOBAL ones, kept apart from the session modes an
        # instruction restores into self.skills (see _skills_for_tree).
        skills = load_skills_tree(SKILLS_DIR)
        self._skill_global_modes = {n: e.get("mode", "disabled")
                                    for n, e in skills.items() if isinstance(e, dict)}
        return skills

    def _save_skills(self):
        # Diff-aware and WRITE-ONLY (never deletes folders) — deletion is an
        # explicit action via delete_skill_tree_entry at the delete callsites.
        save_skills_tree(SKILLS_DIR, self._skills_for_tree())

    def _skills_for_tree(self):
        """self.skills as the tree should record it: each skill with its GLOBAL
        mode. An instruction's skill modes are session-only
        (_restore_skill_modes writes them into self.skills), so a save must
        not carry them into the shared tree — it used to: any later skill save
        wrote every skill's session mode as its global one. A mode reaches the
        tree only through _set_skill_mode (Cycle Mode, manage_skills
        create / update); a skill the map has not seen yet (a new one) is
        recorded with its own mode. A host that never loaded the tree (a bare
        test host) has no map and writes self.skills as it stands."""
        glob = getattr(self, "_skill_global_modes", None)
        if glob is None:
            return self.skills
        out = {}
        for name, entry in self.skills.items():
            if isinstance(entry, dict):
                mode = glob.setdefault(name, entry.get("mode", "disabled"))
                if entry.get("mode") != mode:
                    entry = dict(entry, mode=mode)
            out[name] = entry
        return out

    def _set_skill_mode(self, name, mode):
        """An explicit mode change (Cycle Mode, manage_skills): the session
        AND the global mode, so the next save writes it to the tree."""
        self.skills[name]["mode"] = mode
        glob = getattr(self, "_skill_global_modes", None)
        if glob is not None:
            glob[name] = mode

    @staticmethod
    def _desc_length_warning(desc):
        """Soft Agent-Skills-spec guideline (<=1024 chars) — warn, never reject."""
        if len(desc) > 1024:
            return (f"\nWarning: description is {len(desc)} chars; the Agent Skills "
                    "guideline is <=1024. Consider shortening it.")
        return ""

    @staticmethod
    def _is_kebab_name(name):
        """Agent-Skills naming rule: lowercase letters/digits/hyphens, <=64
        chars (e.g. 'westpac-login'). Enforced on CREATE only — an existing
        legacy-named skill can still be edited/saved (grandfathered)."""
        return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)) and len(name) <= 64

    @staticmethod
    def _kebabize(name):
        """Best-effort conversion of a free-typed name to kebab-case, offered
        as the suggestion when enforcement rejects ('Test_skill' →
        'test-skill'). Returns '' when nothing usable remains."""
        s = re.sub(r"[ _]+", "-", name.strip().lower())
        s = re.sub(r"[^a-z0-9-]+", "", s)
        s = re.sub(r"-{2,}", "-", s).strip("-")
        return s[:64].rstrip("-")

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
                section = section_of(sd)        # "" = unfiled: no tag
                tag = f"  (section: {section})" if section else ""
                lines.append(f"• {sn}  [{mode}]{tag}\n  {info}")
            return "\n".join(lines)

        if not name:
            return "Error: 'name' is required for this action."

        if action == "read":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            sd = self.skills[name]
            return json.dumps({"name": name, "content": sd.get("content", ""),
                               "mode": sd.get("mode", "disabled"),
                               "description": sd.get("description", ""),
                               "section": section_of(sd)}, indent=2)   # "" = unfiled

        # --- Bundled resource files (2026-09-25): the folder IS the skill —
        # SKILL.md may sit beside references/, scripts/, tests/, … which sync,
        # copy and delete with it. These four actions manage those files by
        # skill name + a RELATIVE file_path, so a model with Meta access can
        # author a COMPLETE skill without the native file tools. file_path is
        # validated by datapaths.skill_resource_path (never absolute, never
        # '..', never the SKILL.md family — that is create/update's job).
        if action == "list_files":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            rels = list_skill_resources(SKILLS_DIR, name)
            if rels is None:
                return (f"Skill '{name}' has no folder on disk yet — its bundled "
                        "files appear after the first save.")
            if not rels:
                return f"Skill '{name}' has no bundled resource files (SKILL.md only)."
            folder = skill_dir_for(SKILLS_DIR, name)
            lines = []
            for rel in rels:
                try:
                    size = os.path.getsize(os.path.join(folder, rel))
                except OSError:
                    size = 0
                lines.append(f"• {rel.replace(os.sep, '/')}  ({size} bytes)")
            return f"Bundled resource files of '{name}':\n" + "\n".join(lines)

        if action == "read_file":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            rel_arg = params.get("file_path", "")
            path, err = skill_resource_path(SKILLS_DIR, name, rel_arg)
            if err:
                return f"Error: {err}."
            if not os.path.isfile(path):
                return (f"Error: '{rel_arg}' does not exist in skill '{name}' "
                        "(use list_files).")
            try:
                with open(path, "rb") as f:
                    raw = f.read(200_001)
            except OSError as e:
                return f"Error reading '{rel_arg}': {e}"
            if b"\x00" in raw[:8192]:
                return (f"'{rel_arg}' is a binary file "
                        f"({os.path.getsize(path)} bytes) — read_file returns text only.")
            text = raw.decode("utf-8", errors="replace")
            if len(raw) > 200_000:
                text = text[:200_000] + "\n...[truncated at 200,000 bytes]"
            return text

        if action == "write_file":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            file_content = params.get("file_content")
            if file_content is None:
                return "Error: 'file_content' is required for write_file."
            if not isinstance(file_content, str):
                return ("Error: 'file_content' must be a string (the file's text) — "
                        f"got {type(file_content).__name__}.")
            rel_arg = params.get("file_path", "")
            path, err = skill_resource_path(SKILLS_DIR, name, rel_arg)
            if err:
                return f"Error: {err}."
            if os.path.isdir(path):
                return f"Error: '{rel_arg}' is a folder in skill '{name}' — name a file."
            existed = os.path.isfile(path)
            tmp = None
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                    f.write(file_content)
                os.replace(tmp, path)
            except (OSError, ValueError) as e:
                # (ValueError: a surrogate the text cannot be encoded with.)
                # The temp file goes too — it used to stay in the skill folder,
                # where it counted as a bundled file, synced and was copied.
                if tmp is not None:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                return f"Error writing '{rel_arg}': {e}"
            verb = "Replaced" if existed else "Wrote"
            return (f"{verb} '{rel_arg}' in skill '{name}' "
                    f"({len(file_content.encode('utf-8'))} bytes).")

        if action == "delete_file":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            rel_arg = params.get("file_path", "")
            path, err = skill_resource_path(SKILLS_DIR, name, rel_arg)
            if err:
                return f"Error: {err}."
            if not os.path.isfile(path):
                return f"Error: '{rel_arg}' does not exist in skill '{name}'."
            try:
                os.remove(path)
            except OSError as e:
                return f"Error deleting '{rel_arg}': {e}"
            # Prune now-empty subdirectories, never the skill folder itself
            folder = skill_dir_for(SKILLS_DIR, name)
            parent = os.path.dirname(path)
            while folder and os.path.realpath(parent) != os.path.realpath(folder):
                try:
                    os.rmdir(parent)   # only succeeds when empty
                except OSError:
                    break
                parent = os.path.dirname(parent)
            return f"Deleted '{rel_arg}' from skill '{name}'."

        if action == "create":
            if name in self.skills:
                return f"Error: Skill '{name}' already exists. Use 'update' to modify it."
            if not self._is_kebab_name(name):
                hint = self._kebabize(name)
                suggestion = f" Try '{hint}'." if hint else ""
                return ("Error: Skill names must be Agent-Skills kebab-case — lowercase "
                        f"letters/digits/hyphens, max 64 chars (e.g. 'westpac-login').{suggestion}")
            content = params.get("content", "")
            if not content:
                return "Error: 'content' is required when creating a skill."
            mode = params.get("mode", "disabled")
            if mode not in ("disabled", "enabled", "on_demand"):
                return f"Error: Invalid mode '{mode}'. Valid modes: disabled, enabled, on_demand."
            section = params.get("section")
            if section is not None and not isinstance(section, str):
                return self._SECTION_TYPE_ERROR
            entry = {"content": content, "mode": mode}
            desc = (params.get("description") or "").strip()
            if desc:
                entry["description"] = desc
            self.skills[name] = entry
            self._set_skill_mode(name, mode)
            # Filing (2026-10-31): the SKILLS list's own rule — the section's
            # last entry, every skill renumbered — the layout keys riding in
            # each SKILL.md's frontmatter through _save_skills.
            if section is not None:
                file_page(self.skills, name, section)
            self._save_skills()
            self._post_skill_ui_refresh()
            return (f"Skill '{name}' created successfully." + self._filing_note(section)
                    + self._desc_length_warning(desc))

        if action == "update":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found. Use 'create' to add it."
            content = params.get("content")
            mode = params.get("mode")
            description = params.get("description")
            section = params.get("section")
            if content is None and mode is None and description is None and section is None:
                return ("Error: At least one of 'content', 'mode', 'description' or "
                        "'section' must be provided for update.")
            if mode is not None and mode not in ("disabled", "enabled", "on_demand"):
                return f"Error: Invalid mode '{mode}'. Valid modes: disabled, enabled, on_demand."
            if section is not None and not isinstance(section, str):
                return self._SECTION_TYPE_ERROR
            if content is not None:
                self.skills[name]["content"] = content
            if mode is not None:
                self._set_skill_mode(name, mode)
            desc = ""
            if description is not None:
                desc = description.strip()
                if desc:
                    self.skills[name]["description"] = desc
                else:
                    self.skills[name].pop("description", None)  # "" clears it
            if section is not None:
                file_page(self.skills, name, section)
            self._save_skills()
            self._post_skill_ui_refresh()
            return (f"Skill '{name}' updated successfully." + self._filing_note(section)
                    + self._desc_length_warning(desc))

        if action == "delete":
            if name not in self.skills:
                return f"Error: Skill '{name}' not found."
            del self.skills[name]
            (getattr(self, "_skill_global_modes", None) or {}).pop(name, None)
            delete_skill_tree_entry(SKILLS_DIR, name)  # _save_skills never deletes
            self._save_skills()
            self._post_skill_ui_refresh()
            return f"Skill '{name}' deleted."

        return f"Error: Unknown action '{action}'."

    _SECTION_TYPE_ERROR = ("Error: 'section' must be a string — the section name to file "
                           "under, '' or 'Unfiled' to unfile.")

    @staticmethod
    def _filing_note(section):
        """The sentence a create / update's reply ends with when the call
        named a section: where the skill now is. '' when it named none."""
        if section is None:
            return ""
        section = normalize_section(section)
        return f" Filed under '{section}'." if section else " Unfiled."

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
                            f"transcript ({name}_<timestamp>.txt) is in {CHATS_DIR}.")
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
        forced_off = []
        for sname in self.skills:
            mode = saved.get(sname)
            if mode is None:
                # Skills absent from the snapshot (created after the
                # instruction was last saved) default to disabled — but SAY so:
                # a skill the user just set [ON] silently vanishing from the
                # system prompt cost a real debugging session (2026-08-07).
                if self.skills[sname].get("mode") != "disabled":
                    forced_off.append(sname)
                self.skills[sname]["mode"] = "disabled"
            elif mode in ("disabled", "enabled", "on_demand"):
                self.skills[sname]["mode"] = mode
        if forced_off:
            names = ", ".join(f"'{n}'" for n in forced_off)
            drift = (f"⚠ Skill(s) {names}: ON in the skills store but absent from this "
                     f"instruction's skill_modes snapshot — disabled for this session. "
                     f"Re-save the instruction with the skill set to include it.\n")
            # Same stash-and-repost channel as provider/model drift: _start_agent
            # clears the output window after an -l restore, so stream_worker
            # re-posts these at run start. Replace any earlier snapshot warning
            # (repeated restores would otherwise stack duplicates).
            if not hasattr(self, "_model_drift_warnings"):
                self._model_drift_warnings = []
            self._model_drift_warnings[:] = [
                w for w in self._model_drift_warnings if "skill_modes snapshot" not in w]
            self._model_drift_warnings.append(drift)
            self._warn(drift)
        # Session-only: applying an instruction's saved skill modes updates the
        # live session (self.skills drives _build_system_prompt) but never the
        # skills tree, the sticky global store — changed only by explicit
        # Skills Manager / manage_skills edits (_set_skill_mode). Nothing here
        # touches _skill_global_modes, so a later skill save still writes each
        # skill's global mode (_skills_for_tree), not the one set here.
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
        """The system prompt a request sends. While a stream_worker runs (a
        MyAgent run, a SelfBot reply) it is the text frozen at its start
        (_freeze_system_prompt), so a change to what the prompt is built
        from — the base prompt, a skill's text, description or mode, a skill
        created or deleted — reaches the NEXT run, never the calls in between.
        The system prompt heads the cached prefix, so a change mid-run made
        the next call re-write the whole prompt cache and, on the models that
        bind thinking blocks to that prefix (Fable 5.1, Mythos 5.1, Opus 5.5),
        drop every earlier thinking block. get_skill reads the live skills,
        so an edited skill's text is there at once."""
        frozen = getattr(self, "_run_system_prompt", None)
        if frozen is not None:
            return frozen
        return self._compose_system_prompt()

    def _compose_system_prompt(self):
        parts = [self.system_prompt]
        for name, skill in self.skills.items():
            if skill.get("mode") == "enabled":
                parts.append(f"## Skill: {name}\n{skill['content']}")
        od_block = self._format_on_demand_listing(self.skills)
        if od_block:
            parts.append(od_block)
        return "\n\n".join(parts)

    def _freeze_system_prompt(self):
        """Fix the system prompt for the stream_worker that is starting."""
        self._run_system_prompt = self._compose_system_prompt()

    def _thaw_system_prompt(self):
        """Let it go when that stream_worker ends: the prompt is composed from
        the live skills again."""
        self._run_system_prompt = None

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

    def _skills_list_width_live(self):
        """The Skills Manager's list pane width — its sash position — while
        the dialog is open and laid out, else None (a pane not yet mapped
        reports 1). _save_last_state keeps the last value it gets."""
        pane = getattr(self, "_skills_list_pane", None)
        try:
            if pane is not None and pane.winfo_exists():
                width = pane.winfo_width()
                return width if width > 1 else None
        except tk.TclError:
            pass
        return None

    def _skills_collapsed(self):
        """The set of SKILLS-list sections the user has collapsed — kept per
        machine in agent_state.json as skills_collapsed_sections, the
        Instructions list's collapsed_sections twin (2026-10-31)."""
        collapsed = getattr(self, "_skills_collapsed_sections", None)
        if collapsed is None:
            collapsed = self._skills_collapsed_sections = set()
        return collapsed

    # ── The Description dialog (2026-10-31) ─────────────────────────────
    #
    # A skill's description — the what + when trigger signal listed in the
    # system prompt's on-demand skills index, one or two sentences, stored in
    # its frontmatter as `description` — is edited in its own dialog, the
    # Instruction Editor's Notes dialog's twin (the user's request; until then
    # a three-row box under the Skills Manager's name row). The DRAFT is the
    # string `_skill_desc_text` while the dialog is closed and the dialog's
    # text box while it is open (`_desc_draft`), so SAVE reads what is typed
    # either way; a skill selection, NEW and DELETE replace it
    # (`_set_desc_draft`). One dialog at a time, over the Skills Manager,
    # opened by the Description button right of Cycle Mode (Alt+E), placed
    # and remembered like the other persisted windows (kind "skill_desc" →
    # `skill_desc_dialog_geometry`), coloured and title-barred by Other Setup
    # like every Toplevel.

    def _desc_box(self):
        """The open Description dialog's text widget, or None."""
        dlg = getattr(self, "_desc_dialog", None)
        box = getattr(self, "_desc_text", None)
        try:
            if dlg is not None and box is not None and dlg.winfo_exists():
                return box
        except tk.TclError:
            pass
        return None

    def _desc_draft(self):
        """The Skills Manager's description draft: the dialog's text while it
        is open, else the string the last skill selection, NEW, DELETE or
        dialog close left."""
        box = self._desc_box()
        if box is not None:
            return box.get("1.0", "end-1c")
        return getattr(self, "_skill_desc_text", "") or ""

    def _set_desc_draft(self, text):
        """Replace the draft — and what an open dialog shows, title included."""
        self._skill_desc_text = text or ""
        box = self._desc_box()
        if box is not None:
            box.delete("1.0", tk.END)
            box.insert("1.0", self._skill_desc_text)
            self._desc_dialog.title(self._desc_title())

    def _desc_title(self):
        """"Description — <skill>" after the Skills Manager's name box (the
        skill loaded, or the name being typed for a new one); "Description"
        with no name."""
        name = ""
        entry = getattr(self, "_skills_name_entry", None)
        try:
            if entry is not None:
                name = entry.get().strip()
        except (AttributeError, tk.TclError):
            name = ""
        return f"Description — {name}" if name else "Description"

    def _open_desc_dialog(self):
        """The Description button: open the dialog over the Skills Manager on
        the draft, or raise the one already open."""
        existing = getattr(self, "_desc_dialog", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                self._desc_text.focus_set()
                return
        except tk.TclError:
            pass
        parent = self.skills_editor_window if (
            self.skills_editor_window and self.skills_editor_window.winfo_exists()
        ) else self.root
        dlg = tk.Toplevel(parent)
        self._desc_dialog = dlg
        dlg.withdraw()  # Hide until geometry is set to prevent flicker/repositioning
        dlg.title(self._desc_title())
        if IS_WINDOWS:
            dlg.transient(parent)
        dlg.resizable(True, True)
        dlg.grid_rowconfigure(1, weight=1)
        dlg.grid_columnconfigure(0, weight=1)

        # The hint wraps to the dialog's width (the Notes dialog's pattern)
        hint = tk.Label(
            dlg, text="One or two sentences: what the skill does and when to use it — the trigger "
                      "the system prompt's on-demand index lists. SAVE keeps it with the skill; "
                      "line breaks become spaces.",
            font=("Arial", 9), anchor="w", justify="left", wraplength=440,
        )
        hint.bind("<Configure>", lambda e: hint.config(wraplength=max(e.width - 4, 200)))
        hint.grid(row=0, column=0, columnspan=2, sticky="ew", padx=15, pady=(12, 6))
        # Prose, in the font the old box had (the Notes dialog's text is code-like)
        self._desc_text = tk.Text(dlg, wrap=tk.WORD, font=("Arial", 10), undo=True)
        self._desc_text.grid(row=1, column=0, sticky="nsew", padx=(15, 0), pady=(0, 5))
        desc_scrollbar = tk.Scrollbar(dlg, command=self._desc_text.yview)
        desc_scrollbar.grid(row=1, column=1, sticky="ns", padx=(0, 15), pady=(0, 5))
        self._desc_text.config(yscrollcommand=desc_scrollbar.set)
        self._desc_text.insert("1.0", getattr(self, "_skill_desc_text", "") or "")

        def close(event=None):
            self._close_desc_dialog(save_state=True)
            return "break"

        close_btn = tk.Button(dlg, text="Close", width=10, command=close)
        close_btn.grid(row=2, column=0, columnspan=2, pady=(5, 12))

        dlg.protocol("WM_DELETE_WINDOW", close)
        # Keyboard operation: Alt+E the text, Alt+C Close; Escape in the text
        # lands on Close (the class binding) and Return presses it — no
        # Escape-to-close, since the box is a draft.
        bind_mnemonics(dlg, {"e": self._desc_text, "c": close_btn})

        # Restore geometry AFTER all content is laid out, then show
        dlg.update_idletasks()
        # Room for the hint and four or five wrapped lines at the default:
        # a two-sentence description runs to that at this width
        self._place_window(dlg, "skill_desc", (560, 280), parent=parent, min_size=(300, 120))
        dlg.deiconify()
        # Initial focus: the text, cursor at the end of the description
        self._desc_text.mark_set("insert", "end-1c")
        self._desc_text.focus_set()

    def _close_desc_dialog(self, save_state=False):
        """Take the dialog's text into the draft, remember its geometry and
        destroy it; a no-op with no dialog open. `save_state` writes the
        state file at once (the dialog's own Close / [X]); the Skills
        Manager's close, which saves itself, passes nothing."""
        dlg = getattr(self, "_desc_dialog", None)
        box = getattr(self, "_desc_text", None)
        self._desc_dialog = None
        self._desc_text = None
        if dlg is None:
            return
        try:
            if not dlg.winfo_exists():
                return
            if box is not None:
                self._skill_desc_text = box.get("1.0", "end-1c")
            self._remember_geometry("skill_desc", dlg)
            dlg.destroy()
        except tk.TclError:
            return
        if save_state:
            try:
                self._save_last_state()
            except Exception:
                pass

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
            # The Description dialog first — its text lands in the draft the
            # close discards with the rest, its geometry is remembered —
            # then the window, which this close saves the state for itself
            self._close_desc_dialog()
            self._remember_geometry("skills", win)
            self._save_last_state()      # reads the list pane's width while it exists
            win.destroy()
            self._skills_list_pane = None
            self._skills_list = None
            self._skills_name_entry = None

        win.protocol("WM_DELETE_WINDOW", _on_skills_close)

        top = tk.Frame(win)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 5))

        tk.Label(top, text="Skill", font=("Arial", 10)).pack(side=tk.LEFT, padx=(0, 5))
        name_entry = tk.Entry(top, font=("Arial", 10), width=20)
        name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        self._skills_name_entry = name_entry     # the Description dialog's title follows it
        self._set_desc_draft("")                  # a fresh Skills Manager: nothing loaded yet

        # The skill whose content the editor currently shows (set by the list
        # selection, cleared by NEW). SAVE under a DIFFERENT, NEW name is a
        # save-as: the loaded skill is its source, and the source's bundled
        # resource files (references/, scripts/, …) are copied into the new
        # folder — the folder is the skill, so a copy that carried only
        # SKILL.md was not a copy of the skill (user report, 2026-09-25).
        loaded = {"name": None}

        def save_skill():
            name = name_entry.get().strip()
            if not name:
                messagebox.showwarning("No name", "Enter a name for the skill.", parent=win)
                return
            # Enforce Agent-Skills kebab-case on NEW names only (an existing
            # legacy-named skill stays editable); offer the auto-converted
            # name so a Title-Case typo is one click from correct.
            if name not in self.skills and not self._is_kebab_name(name):
                hint = self._kebabize(name)
                if not hint:
                    messagebox.showwarning(
                        "Invalid name",
                        "Skill names use Agent-Skills kebab-case: lowercase letters, "
                        "digits and hyphens (e.g. 'westpac-login').", parent=win)
                    return
                if hint in self.skills:
                    prompt = (f"Skill names use kebab-case, and '{hint}' already exists — "
                              f"SAVE will overwrite its content.\n\nSave as '{hint}'?")
                else:
                    prompt = ("Skill names use kebab-case (lowercase letters/digits/"
                              f"hyphens).\n\nCreate as '{hint}' instead?")
                if not messagebox.askyesno("Skill name", prompt, parent=win):
                    return
                name = hint
                name_entry.delete(0, tk.END)
                name_entry.insert(0, name)
            content = text_editor.get("1.0", "end-1c").strip()
            if not content:
                messagebox.showwarning("Empty", "The skill content is empty.", parent=win)
                return
            is_new = name not in self.skills
            # Merge into the existing entry (never whole-entry replace) so fields
            # this editor doesn't show — e.g. one added by a newer version on
            # another machine — survive a SAVE here.
            entry = dict(self.skills.get(name, {}))
            entry["content"] = content
            entry.setdefault("mode", "disabled")
            # The description draft — the Description dialog's text while it
            # is open — whitespace-collapsed: descriptions are single-line
            desc = " ".join(self._desc_draft().split())
            if desc:
                entry["description"] = desc
            else:
                entry.pop("description", None)
            self.skills[name] = entry
            if is_new:
                # Filed where the list's selection is — the selected skill's
                # section, or the selected section — else unfiled, as that
                # section's last row (the Instructions list's SAVE rule); an
                # existing skill keeps its place through the merge above
                section = skills_list.section_for_new(self.skills)
                if section:
                    entry["section"] = section
                renumber(self.skills, rows(self.skills))
            self._save_skills()
            # Save-as: a NEW name while another skill's content is loaded
            # copies the source's bundled resource files into the new folder
            # (never onto an EXISTING skill, whose folder owns its own files).
            src = loaded["name"]
            if is_new and src and src != name and src in self.skills:
                copied = copy_skill_resources(SKILLS_DIR, src, name)
                if copied:
                    messagebox.showinfo(
                        "Bundled files copied",
                        f"Copied {len(copied)} bundled resource file(s) from "
                        f"'{src}' into the new skill's folder.", parent=win)
            loaded["name"] = name           # before the reselect: on_select skips the loaded skill
            refresh_list(select=("page", name))
            self._update_skills_button()

        def delete_skill():
            row = skills_list.selected_row()
            if row and row[0] == "section":
                messagebox.showwarning(
                    "Section selected",
                    "Select a skill, not a section. A section is just the skills "
                    "filed in it — it disappears once they are moved out or deleted.",
                    parent=win)
                return
            name = row[1] if row else ""
            if not name:
                messagebox.showwarning("No selection", "Select a skill to delete.", parent=win)
                return
            if name in self.skills:
                # GUI-only guard (2026-09-02): DELETE removes the skill's whole
                # folder (SKILL.md plus any bundled files) from the OneDrive-
                # shared tree on every machine, with no undo. The manage_skills
                # tool's delete branch is deliberately NOT gated — tool-driven
                # deletes are the model acting on an explicit instruction, and
                # an unattended run has nobody to answer a dialog.
                if not messagebox.askyesno(
                        "Delete skill",
                        f"Permanently delete the skill '{name}'?\n\n"
                        "Its folder (SKILL.md and any bundled files) is removed from the "
                        "shared skills tree on every synced machine. This cannot be undone.",
                        icon="warning", default="no", parent=win):
                    return
                del self.skills[name]
                (getattr(self, "_skill_global_modes", None) or {}).pop(name, None)
                delete_skill_tree_entry(SKILLS_DIR, name)  # _save_skills never deletes
                self._save_skills()
                refresh_list()          # its row goes, and the selection with it
                name_entry.delete(0, tk.END)
                self._set_desc_draft("")
                text_editor.delete("1.0", tk.END)
                if loaded["name"] == name:
                    loaded["name"] = None   # its fields were just cleared too
                self._update_skills_button()

        def new_skill():
            name_entry.delete(0, tk.END)
            self._set_desc_draft("")
            text_editor.delete("1.0", tk.END)
            skills_list.select(None)
            loaded["name"] = None   # a fresh skill has no save-as source

        # The three buttons hug the right edge (their frame packs RIGHT; the
        # name_entry above, fill=X + expand, absorbs the space in between) but
        # are created left-to-right INSIDE it, so Tab order matches the visual
        # SAVE, DELETE, NEW — creation order is Tab order. Their font is the
        # Instruction Editor's top row's (SMALL_BUTTON_FONT, since 2026-10-23),
        # so the two dialogs' buttons stand at one height.
        btn_row = tk.Frame(top)
        btn_row.pack(side=tk.RIGHT)
        save_btn = tk.Button(btn_row, text="SAVE", command=save_skill, width=6,
                             font=SMALL_BUTTON_FONT)
        save_btn.pack(side=tk.LEFT, padx=2)
        delete_btn = tk.Button(btn_row, text="DELETE", command=delete_skill, width=7,
                               font=SMALL_BUTTON_FONT)
        delete_btn.pack(side=tk.LEFT, padx=2)
        new_btn = tk.Button(btn_row, text="NEW", command=new_skill, width=5,
                            font=SMALL_BUTTON_FONT)
        new_btn.pack(side=tk.LEFT, padx=2)

        # The list and the skill text share a sash (2026-10-23 — the
        # Instruction Editor's bottom row's twin): a PanedWindow whose list
        # pane's width is remembered per instance in agent_state.json as
        # skills_list_width (_save_last_state reads it off the pane while
        # the dialog is open, so the close and the periodic save both keep
        # it) and restored here; the first opening takes the 40-column
        # list's natural width (tkinter drops a None option). Row 1 since
        # 2026-10-31: the three-row Description box that stood between the
        # name row and the panes became the Description dialog (below).
        paned = tk.PanedWindow(win, orient=tk.HORIZONTAL, sashwidth=5,
                               sashrelief="raised", bd=0)
        paned.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=10, pady=(0, 10))
        self._skills_list_pane = left = tk.Frame(paned)
        right = tk.Frame(paned)
        paned.add(left, width=getattr(self, "_skills_list_width", None), minsize=150)
        paned.add(right, minsize=200)   # the stretchy pane (Tk stretches the last)
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        # Cycle Mode sits ABOVE the list, sized to its label and left-aligned,
        # and since 2026-10-31 the Description button (the user's request)
        # sits to its right: the Instruction Editor's Notes button's twin, it
        # opens the Description dialog over the description draft
        # (_open_desc_dialog) — where a three-row box under the name row
        # showed the description until then. Created in that order, so Tab
        # runs Cycle Mode → Description → the list.
        mode_row = tk.Frame(left)
        mode_row.grid(row=0, column=0, sticky="w", pady=(0, 5))
        toggle_btn = tk.Button(mode_row, text="Cycle Mode", font=("Arial", 9))
        toggle_btn.pack(side=tk.LEFT)
        desc_btn = tk.Button(mode_row, text="Description", font=("Arial", 9),
                             command=self._open_desc_dialog)
        desc_btn.pack(side=tk.LEFT, padx=(5, 0))

        # The SKILLS list (2026-10-31): the Instructions list's twin — a
        # SectionedList (myagent/sectioned_list.py) over self.skills, every
        # skill a page in a section, its place carried in its SKILL.md
        # frontmatter as section / order (datapaths.SKILL_LAYOUT_KEYS) —
        # under the SKILLS band (2026-09-12, see ui_mixin.list_title_band),
        # with the ▲ / ▼ / Section… buttons under the list; until then a
        # plain Listbox in the tree's folder order. Each row still carries
        # the skill's mode as the listbox did: an [ON] / [OD] prefix and a
        # colour (green / blue) by tag; the row's id stays the bare name.
        list_frame = tk.Frame(left)
        list_frame.grid(row=1, column=0, sticky="nsew")

        def row_text(name, entry):
            mode = (entry or {}).get("mode", "disabled")
            prefix = {"enabled": "[ON] ", "on_demand": "[OD] "}.get(mode, "     ")
            return f"{prefix}{name}"

        def row_tags(name, entry):
            mode = (entry or {}).get("mode", "disabled")
            return (mode,) if mode in ("enabled", "on_demand") else ()

        def on_select(event=None):
            # <<TreeviewSelect>>: load the selected skill into the fields. Tk
            # QUEUES the event (every pending one sees the final selection),
            # so the list's own reselections — after a SAVE, a move, Cycle
            # Mode — are told apart by name: the skill already loaded is not
            # loaded again, which also keeps an edit in progress (the
            # Instructions list's _instr_shown_name rule).
            name = skills_list.selected_name()
            if not name or name == loaded["name"] or name not in self.skills:
                return
            name_entry.delete(0, tk.END)
            name_entry.insert(0, name)
            # After the name: the Description dialog's title follows it
            self._set_desc_draft(self.skills[name].get("description", ""))
            text_editor.delete("1.0", tk.END)
            text_editor.insert("1.0", self.skills[name]["content"])
            loaded["name"] = name

        def focus_text():
            # Enter on a skill: it is loaded, so the focus goes to its text
            text_editor.mark_set("insert", "end-1c")
            text_editor.focus_set()

        self._skills_list = skills_list = SectionedList(
            list_frame, title="SKILLS", noun="skill",
            load=lambda: self.skills, save=lambda store: self._save_skills(),
            collapsed=self._skills_collapsed, parent=lambda: win,
            place_window=lambda *args, **kwargs: self._place_window(*args, **kwargs),
            on_select=on_select, on_return=focus_text,
            row_text=row_text, row_tags=row_tags, colors=theme_colors(self))
        skills_list.tree.tag_configure("enabled", foreground="#2e7d32")
        skills_list.tree.tag_configure("on_demand", foreground="#1565c0")

        def refresh_list(select=None):
            # Rebuild the list from self.skills, keeping the selection (or
            # moving it to `select`). Also called from outside the dialog
            # when a mode changes (_post_skill_ui_refresh, _restore_skill_modes).
            skills_list.refresh(self.skills, select=select)

        self._skills_refresh_list = refresh_list

        def toggle_skill():
            name = skills_list.selected_name()
            if not name:
                messagebox.showwarning("No selection", "Select a skill to toggle.", parent=win)
                return
            if name in self.skills:
                cycle = {"disabled": "enabled", "enabled": "on_demand", "on_demand": "disabled"}
                cur = self.skills[name].get("mode", "disabled")
                self._set_skill_mode(name, cycle.get(cur, "disabled"))
                self._save_skills()
                refresh_list()      # the row's prefix and colour follow; the selection stays
                self._update_skills_button()

        def _cycle_on_space(event):
            # Space on a skill mirrors the Cycle Mode button; on a section
            # header it is Tk's own fold / unfold (the Treeview class binding).
            if not skills_list.selected_name():
                return None
            toggle_skill()
            return "break"

        skills_list.tree.bind("<space>", _cycle_on_space)
        toggle_btn.config(command=toggle_skill)

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

        # Keyboard operation (myagent/keyboard.py): Alt+letter for the
        # buttons and fields (the letters are tabled in the README — nothing
        # is underlined), Ctrl+S = SAVE; Space on the list cycles the
        # selected skill's mode (above); the list's own keys (Enter, Alt+Up /
        # Alt+Down) are bound by SectionedList; Alt+E, the description box's
        # letter, presses the Description button. Escape / Ctrl+Tab leave the
        # text boxes (class binding); the window never closes on Escape,
        # since closing discards unsaved edits.
        bind_mnemonics(win, {
            "s": save_btn, "d": delete_btn, "n": new_btn, "c": toggle_btn,
            "k": name_entry, "e": desc_btn,
            "l": skills_list.tree, "t": text_editor,
            "o": skills_list.section_btn,
        })

        def _save_key(event):
            save_skill()
            return "break"

        win.bind("<Control-s>", _save_key)

        # Restore geometry AFTER all content is laid out, then show
        win.update_idletasks()
        self._place_window(win, "skills", (900, 500), parent=parent, min_size=(400, 300))
        win.deiconify()
        skills_list.tree.focus_set()  # initial focus: browse the skills with the arrow keys
