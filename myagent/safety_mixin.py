import os, re, signal, subprocess, threading, tkinter as tk
from tkinter import messagebox

from myagent.constants import (
    IS_WINDOWS, _SUBPROCESS_NOWND, COMMAND_BLOCKED, COMMAND_CONFIRM,
    GMAIL_CONFIRM_TOOLS, PROTON_CONFIRM_TOOLS, OUTLOOK_CONFIRM_TOOLS,
    MONO_FONT,
    _HAS_GOOGLE, _HAS_PROTONMAIL, _HAS_OUTLOOK,
)
from myagent.helpers import extract_text_from_html, input_wait_timer

from ddgs import DDGS
import httpx


class SafetyMixin:

    # ── Agent Start / Stop ──────────────────────────────────────────────

    def _start_agent(self):
        if self.streaming:
            return
        if not self.agent_instruction.strip():
            messagebox.showwarning("No instruction", "Set an Agent Instruction before starting.")
            return

        # Reset for a new run
        self.messages = []
        self.stop_requested = False
        self.chat_display.config(state="normal")
        self.chat_display.delete("1.0", tk.END)
        self.chat_display.config(state="disabled")

        user_text = self.agent_instruction.strip()

        # Build content with the instruction's images (keep originals for re-runs)
        images = list(self.pending_images)
        if images:
            content = []
            for image_data, media_type, _filename in images:
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                })
            content.append({"type": "text", "text": user_text})
            filenames = [img[2] for img in images]
            self.append_message("user", user_text, filenames=filenames)
        else:
            content = user_text
            self.append_message("user", user_text)

        self.messages.append({"role": "user", "content": content})
        self.streaming = True
        self._start_button.config(state="disabled")
        self._stop_button.config(state="normal")
        self.instruction_button.config(state="disabled")

        thread = threading.Thread(
            target=self.stream_worker, args=(self.messages,), daemon=True
        )
        thread.start()

    def _stop_agent(self):
        self.stop_requested = True
        self._stop_button.config(state="disabled")

    def append_message(self, role, content, filenames=None):
        self.chat_display.config(state="normal")
        if role == "user":
            self.chat_display.insert(tk.END, "Instruction:\n", "user_label")
            if filenames:
                for name in filenames:
                    self.chat_display.insert(tk.END, f"[Image: {name}] ", "image_info")
            self.chat_display.insert(tk.END, content + "\n\n", "user")
        self.chat_display.see(tk.END)
        self.chat_display.config(state="disabled")

    # ── Core Tools ──────────────────────────────────────────────────────

    def search_web(self, query):
        try:
            results = DDGS().text(query, max_results=5)
            if not results:
                return "No results found."
            return "\n".join(f"Title: {r['title']}\nURL: {r['href']}\nSnippet: {r['body']}\n"
                             for r in results)
        except Exception as e:
            return f"Search error: {e}"

    def fetch_url(self, url):
        try:
            response = httpx.get(url, follow_redirects=True, timeout=15)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "html" in content_type:
                text = extract_text_from_html(response.text)
            else:
                text = response.text
            if len(text) > 20000:
                text = text[:20000] + "\n\n[Content truncated...]"
            return text
        except Exception as e:
            return f"Error fetching URL: {e}"

    def _open_ps_safety_dialog(self):
        parent = self.instruction_editor_window if (
            self.instruction_editor_window and self.instruction_editor_window.winfo_exists()
        ) else self.root
        dlg = tk.Toplevel(parent)
        self._ps_safety_dialog = dlg
        dlg.withdraw()  # Hide until geometry is set to prevent flicker/repositioning
        dlg.title("Safety — Confirm Patterns")
        if IS_WINDOWS:
            dlg.transient(parent)
        dlg.resizable(True, True)

        tk.Label(
            dlg, text="Checked items require confirmation before execution.\n"
                       "Uncheck to bypass the confirmation dialog. Command\n"
                       "patterns are matched by regex; Gmail entries match the tool name.",
            font=("Arial", 9), justify="left",
        ).pack(padx=15, pady=(12, 6), anchor="w")

        # Use a Text widget with embedded checkbuttons for reliable scrolling
        text_frame = tk.Frame(dlg)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=(0, 5))
        scrollbar = tk.Scrollbar(text_frame, orient="vertical")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text_widget = tk.Text(
            text_frame, wrap="none", cursor="arrow",
            yscrollcommand=scrollbar.set, highlightthickness=0,
            borderwidth=1, relief="sunken",
        )
        scrollbar.config(command=text_widget.yview)
        text_widget.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Section 1: shell command patterns (regex-based, matched in _check_command_safety)
        shell_label = "── PowerShell command patterns ──" if IS_WINDOWS else "── Shell command patterns ──"
        text_widget.insert("end", shell_label + "\n")
        for pattern in COMMAND_CONFIRM:
            var = tk.BooleanVar(value=pattern not in self._disabled_confirm_patterns)
            cb = tk.Checkbutton(
                text_widget, text=pattern, variable=var, font=(MONO_FONT, 9),
                anchor="w", bg="white", activebackground="white",
                command=lambda p=pattern, v=var: self._toggle_confirm_pattern(p, v),
            )
            text_widget.window_create("end", window=cb, stretch=True)
            text_widget.insert("end", "\n")

        # Section 2: Gmail destructive tools (tool-name match, checked in _confirm_gmail_action).
        # Only shown when google support is available — keeps the dialog clean
        # for users who don't have google-api-python-client installed.
        if _HAS_GOOGLE:
            text_widget.insert("end", "\n── Gmail destructive tools ──\n")
            for tool_name in GMAIL_CONFIRM_TOOLS:
                var = tk.BooleanVar(value=tool_name not in self._disabled_confirm_patterns)
                cb = tk.Checkbutton(
                    text_widget, text=tool_name, variable=var, font=(MONO_FONT, 9),
                    anchor="w", bg="white", activebackground="white",
                    command=lambda p=tool_name, v=var: self._toggle_confirm_pattern(p, v),
                )
                text_widget.window_create("end", window=cb, stretch=True)
                text_widget.insert("end", "\n")

        # Section 3: IMAP mail destructive tools (Proton Bridge, WebCentral,
        # any IMAP/SMTP account) — same checkbox pattern, checked in
        # _confirm_proton_action. The tools are still named proton_* for
        # backward compatibility with existing instructions, but they route
        # through whichever account the agent picks from accounts.json.
        # DISPLAY LABEL vs STORED PATTERN: the checkbox shows "IMAP_send"
        # (etc.) for user-facing accuracy — the integration is no longer
        # Proton-specific. But the lambda binds p=tool_name with the
        # original "proton_send" string, so _disabled_confirm_patterns
        # still stores and checks against the actual tool name. The split
        # between display text and stored value is cosmetic-only and does
        # not affect the bypass-detection code path in _confirm_proton_action.
        if _HAS_PROTONMAIL:
            text_widget.insert("end", "\n── IMAP mail destructive tools ──\n")
            for tool_name in PROTON_CONFIRM_TOOLS:
                var = tk.BooleanVar(value=tool_name not in self._disabled_confirm_patterns)
                display_label = tool_name.replace("proton_", "IMAP_", 1)
                cb = tk.Checkbutton(
                    text_widget, text=display_label, variable=var, font=(MONO_FONT, 9),
                    anchor="w", bg="white", activebackground="white",
                    command=lambda p=tool_name, v=var: self._toggle_confirm_pattern(p, v),
                )
                text_widget.window_create("end", window=cb, stretch=True)
                text_widget.insert("end", "\n")

        # Section 4: Outlook / Microsoft 365 destructive tools (Microsoft Graph)
        # — same checkbox + per-instruction bypass pattern, checked in
        # _confirm_outlook_action. Only shown when msal is installed.
        if _HAS_OUTLOOK:
            text_widget.insert("end", "\n── Outlook destructive tools ──\n")
            for tool_name in OUTLOOK_CONFIRM_TOOLS:
                var = tk.BooleanVar(value=tool_name not in self._disabled_confirm_patterns)
                cb = tk.Checkbutton(
                    text_widget, text=tool_name, variable=var, font=(MONO_FONT, 9),
                    anchor="w", bg="white", activebackground="white",
                    command=lambda p=tool_name, v=var: self._toggle_confirm_pattern(p, v),
                )
                text_widget.window_create("end", window=cb, stretch=True)
                text_widget.insert("end", "\n")

        text_widget.configure(state="disabled")

        def _on_close():
            self._last_ps_safety_geometry = dlg.geometry()
            self._ps_safety_dialog = None
            self._save_last_state()
            dlg.destroy()

        dlg.protocol("WM_DELETE_WINDOW", _on_close)

        # Set geometry AFTER layout but BEFORE showing to prevent WM repositioning
        dlg.update_idletasks()
        saved_geo = getattr(self, '_last_ps_safety_geometry', None)
        if saved_geo:
            geo = self._sanitize_geometry(saved_geo)
        else:
            w, h = 560, 1100
            x = parent.winfo_x() + (parent.winfo_width() - w) // 2
            y = parent.winfo_y() + (parent.winfo_height() - h) // 2
            geo = f"{w}x{h}+{x}+{y}"
        # Apply geometry twice: before and after deiconify, because the embedded
        # checkbuttons in the Text widget request a large natural size that
        # overrides the width/height on map. The delayed re-apply wins.
        # Use after(100ms) instead of after_idle — on macOS the WM repositions
        # transient windows asynchronously after deiconify, so after_idle fires
        # too early and gets overridden.
        dlg.geometry(geo)
        dlg.deiconify()
        dlg.after(100, lambda: dlg.geometry(geo) if dlg.winfo_exists() else None)

    def _toggle_confirm_pattern(self, pattern, var):
        if var.get():
            self._disabled_confirm_patterns.discard(pattern)
        else:
            self._disabled_confirm_patterns.add(pattern)
        self._update_ps_safety_button()
        self._save_last_state()

    def _check_command_safety(self, command):
        for pattern in COMMAND_BLOCKED:
            if re.search(pattern, command, re.IGNORECASE):
                return "blocked", f"BLOCKED: Command matches dangerous pattern ({pattern})"
        for pattern in COMMAND_CONFIRM:
            if re.search(pattern, command, re.IGNORECASE):
                if pattern in self._disabled_confirm_patterns:
                    return "skipped", pattern
                return "confirm", pattern
        return "safe", ""

    def _request_confirmation(self, command, matched_pattern=""):
        event = threading.Event()
        result_holder = [False]

        def ask():
            dlg = tk.Toplevel(self.root)
            self._confirm_dialog = dlg
            dlg.withdraw()  # Hide until geometry is set
            dlg.title("PowerShell — Confirm Command")
            if not self._headless:
                if IS_WINDOWS:
                    dlg.transient(self.root)
                dlg.grab_set()
            else:
                dlg.lift()
                dlg.focus_force()
            dlg.resizable(True, True)

            row = 0
            dlg.grid_columnconfigure(0, weight=1)

            tk.Label(
                dlg, text="The following command requires your approval:",
                font=("Arial", 10), wraplength=450, justify="left",
            ).grid(row=row, column=0, sticky="w", padx=15, pady=(15, 5))
            row += 1

            if matched_pattern:
                tk.Label(
                    dlg, text=f"Triggered by:  {matched_pattern}",
                    font=(MONO_FONT, 9), fg="#cc3300", wraplength=450, justify="left",
                ).grid(row=row, column=0, sticky="w", padx=15, pady=(0, 5))
                row += 1

            dlg.grid_rowconfigure(row, weight=1)
            text_frame = tk.Frame(dlg)
            text_frame.grid(row=row, column=0, sticky="nsew", padx=15, pady=5)
            text_frame.grid_rowconfigure(0, weight=1)
            text_frame.grid_columnconfigure(0, weight=1)
            row += 1

            cmd_text = tk.Text(
                text_frame, wrap=tk.WORD, font=(MONO_FONT, 10),
                relief="sunken", bd=1, height=10,
            )
            cmd_text.grid(row=0, column=0, sticky="nsew")
            cmd_sb = tk.Scrollbar(text_frame, command=cmd_text.yview)
            cmd_sb.grid(row=0, column=1, sticky="ns")
            cmd_text.config(yscrollcommand=cmd_sb.set)
            cmd_text.insert("1.0", command)
            cmd_text.config(state="disabled")

            tk.Label(
                dlg, text="Allow execution?", font=("Arial", 10),
            ).grid(row=row, column=0, pady=(5, 5))
            row += 1

            btn_frame = tk.Frame(dlg)
            btn_frame.grid(row=row, column=0, pady=(0, 15))

            def _capture_geo():
                try:
                    self._last_confirm_dialog_geometry = dlg.geometry()
                except Exception:
                    pass
                self._confirm_dialog = None

            def on_yes():
                result_holder[0] = True
                _capture_geo()
                event.set()
                dlg.destroy()

            def on_no():
                result_holder[0] = False
                _capture_geo()
                event.set()
                dlg.destroy()

            tk.Button(btn_frame, text="Deny", command=on_no, width=10).pack(side=tk.LEFT, padx=10)
            tk.Button(btn_frame, text="Allow", command=on_yes, width=10).pack(side=tk.LEFT, padx=10)

            dlg.protocol("WM_DELETE_WINDOW", on_no)

            # Restore geometry AFTER all content is laid out to prevent layout shifts
            dlg.update_idletasks()
            saved_geo = getattr(self, '_last_confirm_dialog_geometry', None)
            if saved_geo:
                dlg.geometry(self._sanitize_geometry(saved_geo))
            else:
                w = max(dlg.winfo_reqwidth(), 500)
                h = min(dlg.winfo_reqheight(), 400)
                x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
                y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
                dlg.geometry(f"{w}x{h}+{x}+{y}")
            dlg.deiconify()  # Show with correct geometry

        self.root.after(0, ask)
        # Time parked on the user's Allow/Deny doesn't count as run time
        # (cost log TIME(sec)) — see helpers.input_wait_timer.
        with input_wait_timer(self):
            event.wait()
        return result_holder[0]

    def do_user_prompt(self, message):
        """Pause the agent and ask the user for input via a modal dialog.

        Returns the reply text; any images the user attached are stashed on
        self._prompt_attached_images for the caller to pop via
        _take_prompt_images() (same thread — the streaming worker blocks on
        the dialog and reads the result synchronously)."""
        event = threading.Event()
        result_holder = ["", []]  # [reply_text, [(b64, media_type, filename)]]

        def ask():
            dlg = tk.Toplevel(self.root)
            self._prompt_dialog = dlg
            dlg.withdraw()  # Hide until geometry is set
            dlg.title("Agent Request")
            if not self._headless:
                if IS_WINDOWS:
                    dlg.transient(self.root)
                dlg.grab_set()
            else:
                dlg.lift()
                dlg.focus_force()
            dlg.resizable(True, True)

            dlg.grid_rowconfigure(1, weight=1)
            dlg.grid_rowconfigure(3, weight=1)
            dlg.grid_columnconfigure(0, weight=1)

            tk.Label(
                dlg, text="The agent is requesting your input:",
                font=("Arial", 10), anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=15, pady=(15, 5))

            msg_frame = tk.Frame(dlg)
            msg_frame.grid(row=1, column=0, sticky="nsew", padx=15, pady=5)
            msg_frame.grid_rowconfigure(0, weight=1)
            msg_frame.grid_columnconfigure(0, weight=1)

            msg_text = tk.Text(
                msg_frame, wrap=tk.WORD, font=(MONO_FONT, 10),
                relief="sunken", bd=1, height=6,
            )
            msg_text.grid(row=0, column=0, sticky="nsew")
            msg_sb = tk.Scrollbar(msg_frame, command=msg_text.yview)
            msg_sb.grid(row=0, column=1, sticky="ns")
            msg_text.config(yscrollcommand=msg_sb.set)
            msg_text.insert("1.0", message)
            msg_text.config(state="disabled")

            tk.Label(
                dlg, text="Your response (Ctrl+Enter for newline):", font=("Arial", 10), anchor="w",
            ).grid(row=2, column=0, sticky="w", padx=15, pady=(10, 2))

            resp_frame = tk.Frame(dlg)
            resp_frame.grid(row=3, column=0, sticky="nsew", padx=15, pady=5)
            resp_frame.grid_rowconfigure(0, weight=1)
            resp_frame.grid_columnconfigure(0, weight=1)

            resp_text = tk.Text(
                resp_frame, wrap=tk.WORD, font=(MONO_FONT, 10),
                relief="sunken", bd=1, height=6,
            )
            resp_text.grid(row=0, column=0, sticky="nsew")
            resp_sb = tk.Scrollbar(resp_frame, command=resp_text.yview)
            resp_sb.grid(row=0, column=1, sticky="ns")
            resp_text.config(yscrollcommand=resp_sb.set)

            # Image attachment row — mirrors the instruction editor's
            # Attach/Remove pattern so a reply can carry images back to the
            # model (they ride the tool_result / user message as standard
            # image blocks, same as screenshots).
            attached_images = []  # (b64_data, media_type, filename)

            img_frame = tk.Frame(dlg)
            img_frame.grid(row=4, column=0, sticky="ew", padx=15, pady=(5, 10))
            img_frame.grid_columnconfigure(2, weight=1)

            img_listbox = tk.Listbox(img_frame, height=3, exportselection=False)

            def _refresh_prompt_images():
                img_listbox.delete(0, tk.END)
                for _data, _mt, filename in attached_images:
                    img_listbox.insert(tk.END, filename)

            def on_attach_images():
                attached_images.extend(self._pick_image_files(parent=dlg))
                _refresh_prompt_images()

            def on_remove_images():
                for idx in reversed(list(img_listbox.curselection())):
                    del attached_images[idx]
                _refresh_prompt_images()

            tk.Button(
                img_frame, text="Attach Images", command=on_attach_images, width=15,
            ).grid(row=0, column=0, padx=(0, 5), sticky="nw")
            tk.Button(
                img_frame, text="Remove Selected", command=on_remove_images, width=15,
            ).grid(row=1, column=0, padx=(0, 5), pady=(5, 0), sticky="nw")
            img_listbox.grid(row=0, column=2, rowspan=2, sticky="ew")

            def _capture_and_close():
                """Save dialog geometry before destroying."""
                try:
                    self._last_prompt_dialog_geometry = dlg.geometry()
                except Exception:
                    pass
                self._prompt_dialog = None

            def on_inject(ev=None):
                text = resp_text.get("1.0", tk.END).strip()
                # An image-only reply is still a reply — don't let the
                # empty-text path stop the agent.
                if not text and attached_images:
                    text = "[See attached image(s)]"
                result_holder[0] = text
                result_holder[1] = list(attached_images)
                _capture_and_close()
                event.set()
                dlg.destroy()
                return "break"

            def on_close():
                result_holder[0] = "[User dismissed the dialog without responding]"
                _capture_and_close()
                event.set()
                dlg.destroy()

            def on_newline(ev=None):
                resp_text.insert(tk.INSERT, "\n")
                return "break"

            resp_text.bind("<Return>", on_inject)
            resp_text.bind("<Control-Return>", on_newline)
            resp_text.bind("<Control-KP_Enter>", on_newline)
            dlg.protocol("WM_DELETE_WINDOW", on_close)

            # Restore geometry AFTER all content is laid out to prevent layout shifts
            dlg.update_idletasks()
            saved_geo = getattr(self, '_last_prompt_dialog_geometry', None)
            if saved_geo:
                dlg.geometry(self._sanitize_geometry(saved_geo))
            else:
                w = max(dlg.winfo_reqwidth(), 500)
                h = max(dlg.winfo_reqheight(), 400)
                x = self.root.winfo_x() + (self.root.winfo_width() - w) // 2
                y = self.root.winfo_y() + (self.root.winfo_height() - h) // 2
                dlg.geometry(f"{w}x{h}+{x}+{y}")
            dlg.deiconify()  # Show with correct geometry

            resp_text.focus_set()

        self.root.after(0, ask)
        # Time parked on the user's reply doesn't count as run time
        # (cost log TIME(sec)) — see helpers.input_wait_timer.
        with input_wait_timer(self):
            event.wait()
        # Echo the user's response in the chat display so it's visible
        response = result_holder[0]
        self._prompt_attached_images = list(result_holder[1])
        if response and response != "[User dismissed the dialog without responding]":
            echo = response
            if result_holder[1]:
                names = ", ".join(fn for _d, _mt, fn in result_holder[1])
                echo += f"\n[Attached image(s): {names}]"
            self.queue.put({"type": "user_prompt_echo", "content": echo})
        return response

    def _take_prompt_images(self):
        """Pop the images attached in the last Agent Request dialog."""
        images = getattr(self, "_prompt_attached_images", [])
        self._prompt_attached_images = []
        return images

    @staticmethod
    def _prompt_image_blocks(images):
        """(b64, media_type, filename) tuples -> Anthropic-style image blocks
        (the internal history format; provider translators handle the rest)."""
        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": image_data,
                },
            }
            for image_data, media_type, _filename in images
        ]

    @staticmethod
    def _kill_process_tree(proc):
        """Kill proc and every descendant. A plain proc.kill() leaves
        grandchildren alive holding the inherited stdout/stderr pipes, which
        keeps communicate() blocked forever (a nested 'powershell -File'
        spawning cmd -> cl.exe reproduced this as an indefinite agent hang)."""
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    capture_output=True, timeout=15, **_SUBPROCESS_NOWND,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def run_powershell(self, command, timeout=30):
        safety, info = self._check_command_safety(command)
        if safety == "blocked":
            return info
        if safety == "skipped":
            self.queue.put({"type": "warning", "content": f"\u26a0 Confirm bypassed (pattern: {info})\n"})
        elif safety == "confirm" and not self._request_confirmation(command, info):
            return "Command was rejected by the user."
        try:
            shell_cmd = (["powershell", "-NoProfile", "-Command", command]
                         if IS_WINDOWS else ["/bin/bash", "-c", command])
            # Popen, not subprocess.run: on timeout the whole process TREE must
            # die before the final pipe read, and POSIX needs its own session
            # (start_new_session) for killpg to reach every descendant.
            popen_kwargs = dict(_SUBPROCESS_NOWND)
            if not IS_WINDOWS:
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen(
                shell_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                **popen_kwargs,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._kill_process_tree(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=10)
                except Exception:
                    stdout, stderr = "", ""
                partial = ""
                if (stdout or "").strip() or (stderr or "").strip():
                    partial = f"\nPartial output before timeout:\n{stdout or ''}"
                    if stderr:
                        partial += f"\nSTDERR:\n{stderr}"
                    if len(partial) > 20000:
                        partial = partial[:20000] + "\n\n[Output truncated...]"
                return (
                    f"Error: Command timed out after {timeout} seconds and its "
                    f"process tree was killed. If the command legitimately needs "
                    f"longer, re-run it with a larger 'timeout' (max 600)." + partial
                )
            output = ""
            if stdout:
                output += stdout
            if stderr:
                output += f"\nSTDERR:\n{stderr}"
            if proc.returncode != 0:
                output += f"\n[Exit code: {proc.returncode}]"
            if len(output) > 20000:
                output = output[:20000] + "\n\n[Output truncated...]"
            return output.strip() if output.strip() else "[No output]"
        except Exception as e:
            return f"Error running command: {e}"
