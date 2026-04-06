import os, io, time, queue, tkinter as tk

from myagent.constants import MONO_FONT, CHATS_DIR

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


class EventLoopMixin:

    def check_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg["type"] == "debug" and not self.debug_enabled.get():
                    pass
                elif msg["type"] == "call_counter" and not self.show_activity.get() and not self.debug_enabled.get() and not self.tool_calls_enabled.get():
                    pass
                elif msg["type"] == "call_counter":
                    tag = "call_counter" if self.debug_enabled.get() else "call_counter_subtle"
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, f"  Call #{msg['content']}  ", tag)
                    self.chat_display.insert(tk.END, "\n", "debug")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "debug":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, "--- PAYLOAD SENT TO API ---\n", "debug_label")
                    self.chat_display.insert(tk.END, msg["content"] + "\n", "debug")
                    self.chat_display.insert(tk.END, "--- END PAYLOAD ---\n\n", "debug_label")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "tool_call_debug" and not self.tool_calls_enabled.get():
                    pass
                elif msg["type"] == "tool_call_debug":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, "--- TOOL CALL ---\n", "tool_debug_label")
                    self.chat_display.insert(tk.END, msg["content"] + "\n", "tool_debug")
                    self.chat_display.insert(tk.END, "--- END TOOL CALL ---\n", "tool_debug_label")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "thinking_start":
                    self._current_thinking_text = ""
                    if self.show_thinking.get():
                        self.chat_display.config(state="normal")
                        self._ensure_newline()
                        self.chat_display.insert(tk.END, "Thinking:\n", "thinking_label")
                        self.chat_display.see(tk.END)
                        self.chat_display.config(state="disabled")
                elif msg["type"] == "thinking_delta":
                    self._current_thinking_text += msg["content"]
                    if self.show_thinking.get():
                        self.chat_display.config(state="normal")
                        self.chat_display.insert(tk.END, msg["content"], "thinking")
                        self.chat_display.see(tk.END)
                        self.chat_display.config(state="disabled")
                elif msg["type"] == "thinking_end":
                    if self.show_thinking.get():
                        self.chat_display.config(state="normal")
                        self.chat_display.insert(tk.END, "\n\n", "thinking")
                        self.chat_display.see(tk.END)
                        self.chat_display.config(state="disabled")
                elif msg["type"] == "label":
                    self._current_response_text = ""
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, "Agent:\n", "assistant_label")
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "text_delta":
                    self._current_response_text += msg["content"]
                    self.chat_display.config(state="normal")
                    self.chat_display.insert(tk.END, msg["content"], "assistant")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "user_prompt_echo":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, "\nYou:\n", "user_label")
                    self.chat_display.insert(tk.END, msg["content"] + "\n\n", "user")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "ci_code" and not self.show_activity.get():
                    pass
                elif msg["type"] == "ci_code":
                    # Code interpreter code — display as a single readable block
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, msg["content"] + "\n", "tool_info")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "ci_image":
                    # Code interpreter image — decode/download, display inline, and save
                    try:
                        url = msg.get("url", "")
                        file_id = msg.get("file_id", "")
                        img_data = None
                        if url and url.startswith("data:"):
                            # data URL: data:image/png;base64,<data>
                            import base64
                            parts = url.split(",", 1)
                            if len(parts) == 2:
                                img_data = base64.b64decode(parts[1])
                        elif url:
                            import urllib.request
                            with urllib.request.urlopen(url, timeout=30) as resp:
                                img_data = resp.read()
                        elif file_id and self.provider == "Anthropic" and hasattr(self, 'client') and self.client:
                            resp = self.client.beta.files.download(file_id)
                            img_data = resp.read()
                        elif file_id and hasattr(self, 'openai_client') and self.openai_client:
                            content = self.openai_client.files.content(file_id)
                            img_data = content.read()
                        if img_data:
                            # Save to saved_chats dir
                            os.makedirs("saved_chats", exist_ok=True)
                            ts = time.strftime("%Y%m%d_%H%M%S")
                            img_path = os.path.join("saved_chats", f"ci_output_{ts}.png")
                            with open(img_path, "wb") as f:
                                f.write(img_data)
                            # Display inline in chat
                            pil_img = Image.open(io.BytesIO(img_data))
                            # Scale to fit chat display width (max ~600px)
                            max_w = 600
                            if pil_img.width > max_w:
                                ratio = max_w / pil_img.width
                                pil_img = pil_img.resize(
                                    (max_w, int(pil_img.height * ratio)),
                                    Image.LANCZOS,
                                )
                            tk_img = ImageTk.PhotoImage(pil_img)
                            # Keep reference to prevent garbage collection
                            if not hasattr(self, '_ci_images'):
                                self._ci_images = []
                            self._ci_images.append(tk_img)
                            self.chat_display.config(state="normal")
                            self._ensure_newline()
                            self.chat_display.image_create(tk.END, image=tk_img)
                            self.chat_display.insert(tk.END, f"\n[Saved: {img_path}]\n", "tool_info")
                            self.chat_display.see(tk.END)
                            self.chat_display.config(state="disabled")
                    except Exception as e:
                        self.chat_display.config(state="normal")
                        self._ensure_newline()
                        self.chat_display.insert(tk.END, f"[Code interpreter image error: {e}]\n", "error")
                        self.chat_display.see(tk.END)
                        self.chat_display.config(state="disabled")
                elif msg["type"] == "tool_info" and not self.show_activity.get():
                    pass
                elif msg["type"] == "tool_info":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, msg["content"], "tool_info")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "warning":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(tk.END, msg["content"], "warning")
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "ensure_newline":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "post_process_latex":
                    self.chat_display.config(state="normal")
                    self._post_process_latex()
                    self.chat_display.config(state="disabled")
                elif msg["type"] == "complete":
                    # Post-process: convert LaTeX to Unicode in the last response
                    self.chat_display.config(state="normal")
                    self._post_process_latex()
                    self.chat_display.insert(tk.END, "\n\n")
                    self.chat_display.config(state="disabled")
                    self.streaming = False
                    self._start_button.config(state="normal")
                    self._stop_button.config(state="disabled")
                    self.instruction_button.config(state="normal")
                elif msg["type"] == "error":
                    self.chat_display.config(state="normal")
                    self._ensure_newline()
                    self.chat_display.insert(
                        tk.END, f"Error: {msg['content']}\n\n", "error"
                    )
                    self.chat_display.see(tk.END)
                    self.chat_display.config(state="disabled")
                    self.streaming = False
                    self._start_button.config(state="normal")
                    self._stop_button.config(state="disabled")
                    self.instruction_button.config(state="normal")
        except queue.Empty:
            pass
        except Exception:
            pass
        self.root.after(50, self.check_queue)

    # ── Window Close ────────────────────────────────────────────────────

    def _on_close(self):
        if getattr(self, '_closing', False):
            return
        self._closing = True
        self.stop_requested = True
        if self.streaming:
            self.root.after(200, self._finish_close)
            return
        self._finish_close()

    def _finish_close(self):
        if self.streaming:
            self.root.after(200, self._finish_close)
            return
        self._save_last_state()
        self._auto_save_on_close()
        self._cleanup_browser()
        self._release_instance_lock()
        self.root.destroy()
