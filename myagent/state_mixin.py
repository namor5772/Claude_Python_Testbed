import os, re, json, time, subprocess, ctypes, tkinter as tk
from tkinter import messagebox
from myagent.constants import IS_WINDOWS, AGENT_LOCK_PREFIX, DEFAULT_GEOMETRY


class StateMixin:

    # ── Instance Management ────────────────────────────────────────────

    @staticmethod
    def _is_pid_alive(pid):
        """Check if a process with the given PID is a running MyAgent.py instance.
        Verifies both that the executable is Python and that its command line
        contains 'MyAgent.py', so other Python processes (VS Code, Claude Code)
        don't falsely hold lock slots."""
        if IS_WINDOWS:
            try:
                kernel32 = ctypes.windll.kernel32
                psapi = ctypes.windll.psapi
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                PROCESS_VM_READ = 0x0010
                handle = kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ, False, pid
                )
                if not handle:
                    return False
                try:
                    # Get the executable name of the process
                    buf = ctypes.create_unicode_buffer(260)
                    if psapi.GetModuleBaseNameW(handle, None, buf, 260):
                        exe_name = buf.value.lower()
                        if exe_name not in ("python.exe", "pythonw.exe"):
                            return False
                    else:
                        return False
                finally:
                    kernel32.CloseHandle(handle)
                # Verify command line contains MyAgent.py
                try:
                    result = subprocess.run(
                        ["wmic", "process", "where", f"ProcessId={pid}",
                         "get", "CommandLine", "/value"],
                        capture_output=True, text=True, timeout=5,
                        creationflags=0x08000000,  # CREATE_NO_WINDOW
                    )
                    return "MyAgent.py" in result.stdout
                except Exception:
                    # If we can't check command line, accept the exe name match
                    return True
            except Exception:
                return False
        else:
            try:
                os.kill(pid, 0)
            except OSError:
                return False
            # PID exists — verify it belongs to a MyAgent.py process
            try:
                result = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "command="],
                    capture_output=True, text=True, timeout=5,
                )
                return "MyAgent.py" in result.stdout
            except Exception:
                return True

    def _claim_instance_number(self):
        """Claim the lowest available instance number via lock files.
        Each instance writes a lock file containing its PID. Stale locks
        (where the PID no longer exists) are cleaned up automatically.

        The claim itself is ATOMIC (O_CREAT|O_EXCL): siblings launched in the
        same instant — parallel run_instruction fan-out spawns children
        simultaneously — must not both claim slot N and then share
        agent_state_N.json / delete each other's lock. The loser of the
        exclusive create gets FileExistsError and moves to the next slot."""
        me = str(os.getpid())
        for num in range(1, 100):
            lock_path = f"{AGENT_LOCK_PREFIX}{num}.lock"
            if os.path.exists(lock_path):
                claimed_by_live = False
                stale_content = None
                try:
                    with open(lock_path) as f:
                        stale_content = f.read().strip()
                    claimed_by_live = self._is_pid_alive(int(stale_content))
                except (ValueError, OSError):
                    # Unreadable PID: a sibling may be mid-claim (its O_EXCL
                    # create landed, its PID write hasn't yet). A FRESH lock is
                    # therefore assumed live; only an old unreadable lock is
                    # stale (crash leftovers don't get younger).
                    try:
                        claimed_by_live = (
                            time.time() - os.path.getmtime(lock_path)) < 5
                    except OSError:
                        pass  # vanished — race for the empty slot below
                if claimed_by_live:
                    continue  # slot taken
                # Judged stale — clear it, but ONLY if it still holds the
                # content we judged: if it changed hands, a sibling claimed
                # the slot in the meantime and the lock is now valid.
                try:
                    with open(lock_path) as f:
                        if (stale_content is not None
                                and f.read().strip() != stale_content):
                            continue  # a sibling owns it now
                    os.remove(lock_path)
                except FileNotFoundError:
                    pass  # a sibling already cleared it
                except OSError:
                    continue  # can't clear it — try the next slot
            # Claim this slot atomically; write the PID on the raw fd right
            # away so the unreadable-lock window above stays microscopic.
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    os.write(fd, me.encode("ascii"))
                finally:
                    os.close(fd)
            except OSError:  # FileExistsError: a sibling won this slot
                continue
            # Verify ownership: a sibling that judged the PREVIOUS occupant
            # stale can have removed our fresh lock inside its judge→remove
            # window. If the file no longer says "us", the slot is theirs.
            try:
                with open(lock_path) as f:
                    if f.read().strip() != me:
                        continue  # clobbered — move on to the next slot
            except OSError:
                continue  # our lock vanished — move on
            self._lock_path = lock_path
            return num
        # Fallback — shouldn't happen
        self._lock_path = None
        return 1

    def _release_instance_lock(self):
        """Remove this instance's lock file."""
        lock_path = getattr(self, '_lock_path', None)
        if lock_path and os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except OSError:
                pass

    # ── State Persistence ───────────────────────────────────────────────

    @staticmethod
    def _get_macos_display_rects():
        """Return list of (left, top, right, bottom) for each display via CoreGraphics."""
        try:
            cg = ctypes.cdll.LoadLibrary(
                '/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')

            class CGPoint(ctypes.Structure):
                _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

            class CGSize(ctypes.Structure):
                _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

            class CGRect(ctypes.Structure):
                _fields_ = [("origin", CGPoint), ("size", CGSize)]

            max_displays = 16
            display_ids = (ctypes.c_uint32 * max_displays)()
            display_count = ctypes.c_uint32()
            cg.CGGetActiveDisplayList(max_displays, display_ids,
                                      ctypes.byref(display_count))

            cg.CGDisplayBounds.restype = CGRect

            rects = []
            for i in range(display_count.value):
                bounds = cg.CGDisplayBounds(display_ids[i])
                l = int(bounds.origin.x)
                t = int(bounds.origin.y)
                r = int(bounds.origin.x + bounds.size.width)
                b = int(bounds.origin.y + bounds.size.height)
                rects.append((l, t, r, b))
            return rects
        except Exception:
            return []

    @staticmethod
    def _get_windows_display_rects():
        """Return list of (left, top, right, bottom) for each display via EnumDisplayMonitors.
        Primary monitor (origin 0,0) is always first."""
        try:
            user32 = ctypes.windll.user32

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            monitors = []
            MONITORENUMPROC = ctypes.WINFUNCTYPE(
                ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                ctypes.POINTER(RECT), ctypes.c_double)

            def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
                r = lprcMonitor[0]
                monitors.append((r.left, r.top, r.right, r.bottom))
                return 1

            user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
            if monitors:
                # Primary monitor has origin (0,0); sort it first, then by position
                monitors.sort(key=lambda r: (r[0] != 0 or r[1] != 0, r[0], r[1]))
                return monitors
        except Exception:
            pass
        return []

    @staticmethod
    def _get_display_rects():
        """Return list of (left, top, right, bottom) for each display. Cross-platform."""
        if IS_WINDOWS:
            return StateMixin._get_windows_display_rects()
        return StateMixin._get_macos_display_rects()

    @staticmethod
    def _get_virtual_screen_bounds():
        """Return (vx, vy, vw, vh) covering all monitors."""
        if IS_WINDOWS:
            try:
                user32 = ctypes.windll.user32
                vx = user32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
                vy = user32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
                vw = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
                vh = user32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
                if vw > 0 and vh > 0:
                    return vx, vy, vw, vh
            except Exception:
                pass
        else:
            # macOS: use CoreGraphics
            rects = StateMixin._get_macos_display_rects()
            if rects:
                min_x = min(r[0] for r in rects)
                min_y = min(r[1] for r in rects)
                max_x = max(r[2] for r in rects)
                max_y = max(r[3] for r in rects)
                vw = max_x - min_x
                vh = max_y - min_y
                if vw > 0 and vh > 0:
                    return min_x, min_y, vw, vh
        # Fallback: primary monitor only
        return 0, 0, 1920, 1080

    @staticmethod
    def _get_monitor_config_key():
        """Return a string key identifying the current monitor layout.

        Uses EnumDisplayMonitors (Windows) or CoreGraphics (macOS) to capture
        each monitor's bounding rect, producing a stable key like
        '0,0,1920,1080|1920,0,3840,1080'. Different setups (docked vs
        undocked, different monitor arrangements) produce different keys,
        enabling per-configuration geometry persistence.
        """
        if IS_WINDOWS:
            try:
                user32 = ctypes.windll.user32

                class RECT(ctypes.Structure):
                    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

                monitors = []

                # EnumDisplayMonitors callback: BOOL CALLBACK(HMONITOR, HDC, LPRECT, LPARAM)
                MONITORENUMPROC = ctypes.WINFUNCTYPE(
                    ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                    ctypes.POINTER(RECT), ctypes.c_double)

                def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
                    r = lprcMonitor[0]
                    monitors.append((r.left, r.top, r.right, r.bottom))
                    return 1  # continue enumeration

                user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(callback), 0)
                if monitors:
                    monitors.sort()
                    return "|".join(f"{l},{t},{r},{b}" for l, t, r, b in monitors)
            except Exception:
                pass
        else:
            # macOS: use CoreGraphics to enumerate displays
            rects = StateMixin._get_macos_display_rects()
            if rects:
                rects.sort()
                return "|".join(f"{l},{t},{r},{b}" for l, t, r, b in rects)
        # Fallback: use virtual screen bounds
        vx, vy, vw, vh = StateMixin._get_virtual_screen_bounds()
        return f"{vx},{vy},{vx + vw},{vy + vh}"

    @staticmethod
    def _sanitize_geometry(geo, min_w=200, min_h=150):
        """Validate a geometry string against the full virtual desktop (all monitors).

        Rejects windows that are too small or positioned entirely off-screen.
        Returns DEFAULT_GEOMETRY if unusable.
        """
        m = re.match(r'(\d+)x(\d+)\+(-?\d+)\+(-?\d+)', geo)
        if not m:
            return DEFAULT_GEOMETRY
        w, h, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        if w < min_w or h < min_h:
            return DEFAULT_GEOMETRY
        # Check against virtual desktop spanning all monitors
        vx, vy, vw, vh = StateMixin._get_virtual_screen_bounds()
        visible_margin = 50
        if x + w < vx + visible_margin or x > vx + vw - visible_margin:
            return DEFAULT_GEOMETRY
        if y + h < vy + visible_margin or y > vy + vh - visible_margin:
            return DEFAULT_GEOMETRY
        return geo

    def _save_last_state(self):
        # Read existing state to preserve geometry entries for other monitor configs
        existing = {}
        if os.path.exists(self._state_file):
            try:
                with open(self._state_file, encoding="utf-8") as f:
                    existing = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        state = {
            "provider": self.provider,
            "last_instruction_name": self.agent_instruction_name,
            "last_model": self.model,
            "temperature": self.temperature,
            "thinking_enabled": self.thinking_enabled,
            "thinking_effort": self.thinking_effort,
            "thinking_budget": self.thinking_budget,
            "thinking_mode": self.thinking_mode,
            "text_verbosity": self.text_verbosity,
            "applied_instruction": {
                "text": self.agent_instruction,
                "images": [
                    {"data": d, "media_type": mt, "filename": fn}
                    for d, mt, fn in getattr(self, "pending_images", [])
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
                # NOTE: skill modes are intentionally NOT snapshotted here. skills.json
                # is the sticky source of truth for skill modes (loaded by _load_skills);
                # duplicating them in this snapshot caused launch to overwrite the user's
                # global modes via _restore_skill_modes. See SkillsMixin._restore_skill_modes.
                "disabled_confirm_patterns": sorted(getattr(self, "_disabled_confirm_patterns", [])),
                "blocked_tools": sorted(getattr(self, "_blocked_tools", [])),
            },
        }
        # Build geometry dict for current monitor configuration
        config_key = self._get_monitor_config_key()
        geo_entry = {"geometry": self.root.geometry()}
        if self.instruction_editor_window and self.instruction_editor_window.winfo_exists():
            geo_entry["editor_geometry"] = self.instruction_editor_window.geometry()
        elif hasattr(self, '_last_editor_geometry') and self._last_editor_geometry:
            geo_entry["editor_geometry"] = self._last_editor_geometry
        # Capture live geometry from open dialogs, fall back to cached values
        ps_dlg = getattr(self, '_ps_safety_dialog', None)
        if ps_dlg and ps_dlg.winfo_exists():
            geo_entry["ps_safety_dialog_geometry"] = ps_dlg.geometry()
        elif getattr(self, '_last_ps_safety_geometry', None):
            geo_entry["ps_safety_dialog_geometry"] = self._last_ps_safety_geometry
        prompt_dlg = getattr(self, '_prompt_dialog', None)
        if prompt_dlg and prompt_dlg.winfo_exists():
            geo_entry["prompt_dialog_geometry"] = prompt_dlg.geometry()
        elif getattr(self, '_last_prompt_dialog_geometry', None):
            geo_entry["prompt_dialog_geometry"] = self._last_prompt_dialog_geometry
        confirm_dlg = getattr(self, '_confirm_dialog', None)
        if confirm_dlg and confirm_dlg.winfo_exists():
            geo_entry["confirm_dialog_geometry"] = confirm_dlg.geometry()
        elif getattr(self, '_last_confirm_dialog_geometry', None):
            geo_entry["confirm_dialog_geometry"] = self._last_confirm_dialog_geometry
        # Capture skills dialog geometry if open, otherwise use last saved
        if self.skills_editor_window and self.skills_editor_window.winfo_exists():
            geo_entry["skills_dialog_geometry"] = self.skills_editor_window.geometry()
        elif getattr(self, '_last_skills_dialog_geometry', None):
            geo_entry["skills_dialog_geometry"] = self._last_skills_dialog_geometry
        # Merge into geometries dict (preserves other monitor configs)
        all_geos = existing.get("geometries", {})
        all_geos[config_key] = geo_entry
        state["geometries"] = all_geos
        # Display checkboxes
        state["show_activity"] = self.show_activity.get()
        state["show_thinking"] = self.show_thinking.get()
        state["save_thinking"] = self.save_thinking.get()
        state["debug_enabled"] = self.debug_enabled.get()
        state["tool_calls_enabled"] = self.tool_calls_enabled.get()
        state["diag_enabled"] = self.diag_enabled.get()
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def _load_last_state(self):
        if not os.path.exists(self._state_file):
            return
        try:
            with open(self._state_file, encoding="utf-8") as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        # Restore instruction (with its images).
        # Prefer the snapshot of the last APPLIED state (survives Apply, not just Save).
        # Fall back to the disk entry for backward compat with older agent_state.json files.
        instr_name = state.get("last_instruction_name", "")
        applied = state.get("applied_instruction")
        model_restored = False
        if applied and applied.get("text"):
            model_restored = self._apply_instruction_entry(instr_name, applied)
        elif instr_name:
            instructions = self._load_saved_instructions()
            if instr_name in instructions:
                model_restored = self._apply_instruction_entry(instr_name, instructions[instr_name])
        if not model_restored:
            # Fall back to state file's model params (for old instructions or no instruction)
            self._restore_model_params(state, state_file=True)
        # Restore geometries for the current monitor configuration
        config_key = self._get_monitor_config_key()
        all_geos = state.get("geometries", {})
        geo_entry = all_geos.get(config_key)
        if not geo_entry and "geometry" in state:
            # Backward compat: migrate old flat geometry fields
            geo_entry = {k: state[k] for k in ("geometry", "editor_geometry",
                         "prompt_dialog_geometry", "confirm_dialog_geometry",
                         "ps_safety_dialog_geometry",
                         "skills_dialog_geometry") if k in state}
        if geo_entry:
            geo = geo_entry.get("geometry")
            if geo:
                self.root.geometry(self._sanitize_geometry(geo))
            editor_geo = geo_entry.get("editor_geometry")
            if editor_geo:
                self._last_editor_geometry = editor_geo
            prompt_geo = geo_entry.get("prompt_dialog_geometry")
            if prompt_geo:
                self._last_prompt_dialog_geometry = prompt_geo
            confirm_geo = geo_entry.get("confirm_dialog_geometry")
            if confirm_geo:
                self._last_confirm_dialog_geometry = confirm_geo
            ps_safety_geo = geo_entry.get("ps_safety_dialog_geometry")
            if ps_safety_geo:
                self._last_ps_safety_geometry = ps_safety_geo
            skills_geo = geo_entry.get("skills_dialog_geometry")
            if skills_geo:
                self._last_skills_dialog_geometry = skills_geo
        # Restore display checkboxes
        if "show_activity" in state:
            self.show_activity.set(state["show_activity"])
        if "show_thinking" in state:
            self.show_thinking.set(state["show_thinking"])
        if "save_thinking" in state:
            self.save_thinking.set(state["save_thinking"])
        if "debug_enabled" in state:
            self.debug_enabled.set(state["debug_enabled"])
        if "tool_calls_enabled" in state:
            self.tool_calls_enabled.set(state["tool_calls_enabled"])
        if "diag_enabled" in state:
            self.diag_enabled.set(state["diag_enabled"])

    def _periodic_save(self):
        try:
            self._save_last_state()
        except Exception:
            pass
        if self.messages:
            try:
                msg_count = len(self.messages)
                if msg_count != getattr(self, '_last_autosaved_msg_count', 0):
                    self._auto_save_on_close()
                    self._last_autosaved_msg_count = msg_count
            except Exception:
                pass
        self.root.after(5000, self._periodic_save)

    def _apply_instruction_entry(self, name, entry):
        """Load an instruction entry into live state. Returns True if model params were restored."""
        self.agent_instruction = entry["text"]
        self.agent_instruction_name = name
        self.pending_images = [
            (img["data"], img["media_type"], img["filename"])
            for img in entry.get("images", [])
        ]
        self.desktop_enabled.set(entry.get("desktop", False))
        self.browser_enabled.set(entry.get("browser", False))
        self.meta_enabled.set(entry.get("meta", False))
        self.mcp_enabled.set(entry.get("mcp", False))
        self.google_enabled.set(entry.get("google", False))
        self.proton_enabled.set(entry.get("proton", False))
        self.outlook_enabled.set(entry.get("outlook", False))
        self.conversational_enabled.set(entry.get("conversational", False))
        model_restored = "model" in entry
        if model_restored:
            self._restore_model_params(entry)
        self._restore_skill_modes(entry)
        self._disabled_confirm_patterns = set(entry.get("disabled_confirm_patterns", []))
        self._blocked_tools = set(entry.get("blocked_tools", []))
        self._update_title()
        return model_restored

    @staticmethod
    def _merge_extra_text(instruction_text, extra_text):
        """Append a per-run task addendum (--extra-file / run_instruction's
        extra_text) to the instruction text under a labeled separator. The saved
        instruction on disk is untouched — this parameterizes one run only."""
        if not extra_text:
            return instruction_text
        return (f"{instruction_text}\n\n"
                "--- ADDITIONAL TASK CONTEXT (for this run only, from the "
                "launching process) ---\n"
                f"{extra_text}")

    def _auto_launch(self):
        """Auto-load an instruction by name and start the agent (from -l arg)."""
        name = self._launch_instruction
        instructions = self._load_saved_instructions()
        if name not in instructions:
            messagebox.showerror(
                "Instruction Not Found",
                f"No saved instruction named '{name}'.\n\n"
                f"Available: {', '.join(sorted(instructions)) or '(none)'}",
            )
            return
        self._apply_instruction_entry(name, instructions[name])
        self.agent_instruction = self._merge_extra_text(
            self.agent_instruction, getattr(self, "_extra_text", ""))
        auto_name = f"{name}_{time.strftime('%Y-%m-%d_%H%M%S')}"
        self.chat_name_entry.delete(0, tk.END)
        self.chat_name_entry.insert(0, auto_name)
        self.root.after(200, self._start_agent)
