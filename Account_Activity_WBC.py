import ctypes
import ctypes.wintypes

# Fix DPI scaling — must run before any window creation.
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import tkinter as tk
from tkinter import ttk
import threading
import queue
import time
import socket
import re
import csv

DEFAULT_BUTTON_TEXT = "Display more"
DEFAULT_CLICK_COUNT = 5
DEFAULT_DELAY_SECONDS = 3
CDP_PORT = 9222


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Account Activity - WBC")
        self.root.resizable(False, False)

        self._queue = queue.Queue()
        self._stop_requested = False
        self._worker_thread = None
        self._playwright = None
        self._browser = None
        self._page = None

        self._setup_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(50, self._check_queue)
        self.root.mainloop()

    def _setup_ui(self):
        pad = dict(padx=8, pady=4)

        # --- Input fields ---
        frame = ttk.Frame(self.root)
        frame.pack(fill="x", **pad)

        ttk.Label(frame, text="Button text:").grid(row=0, column=0, sticky="w", **pad)
        self._btn_text_var = tk.StringVar(value=DEFAULT_BUTTON_TEXT)
        ttk.Entry(frame, textvariable=self._btn_text_var, width=30).grid(row=0, column=1, sticky="ew", **pad)

        ttk.Label(frame, text="Clicks:").grid(row=1, column=0, sticky="w", **pad)
        self._clicks_var = tk.StringVar(value=str(DEFAULT_CLICK_COUNT))
        ttk.Entry(frame, textvariable=self._clicks_var, width=10).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(frame, text="Delay (sec):").grid(row=2, column=0, sticky="w", **pad)
        self._delay_var = tk.StringVar(value=str(DEFAULT_DELAY_SECONDS))
        ttk.Entry(frame, textvariable=self._delay_var, width=10).grid(row=2, column=1, sticky="w", **pad)

        frame.columnconfigure(1, weight=1)

        # --- Buttons ---
        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", **pad)

        self._start_btn = ttk.Button(btn_frame, text="Start", command=self._start)
        self._start_btn.pack(side="left", **pad)

        self._stop_btn = ttk.Button(btn_frame, text="Stop", command=self._stop, state="disabled")
        self._stop_btn.pack(side="left", **pad)

        # --- Status log ---
        self._log_text = tk.Text(self.root, height=12, width=50, state="disabled", wrap="word")
        self._log_text.pack(fill="both", expand=True, **pad)
        self._log_text.tag_configure("error", foreground="red")
        self._log_text.tag_configure("success", foreground="green")
        self._log_text.tag_configure("info", foreground="gray")

    def _log(self, message, tag=None):
        self._log_text.configure(state="normal")
        if tag:
            self._log_text.insert("end", message + "\n", tag)
        else:
            self._log_text.insert("end", message + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _start(self):
        # Validate inputs
        btn_text = self._btn_text_var.get().strip()
        if not btn_text:
            self._log("Button text cannot be empty.", "error")
            return

        try:
            click_count = int(self._clicks_var.get().strip())
            if click_count < 1:
                raise ValueError
        except ValueError:
            self._log("Clicks must be a positive integer.", "error")
            return

        try:
            delay = float(self._delay_var.get().strip())
            if delay < 0:
                raise ValueError
        except ValueError:
            self._log("Delay must be a non-negative number.", "error")
            return

        # Clear log
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

        self._stop_requested = False
        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")

        self._worker_thread = threading.Thread(
            target=self._click_worker, args=(btn_text, click_count, delay), daemon=True
        )
        self._worker_thread.start()

    def _stop(self):
        self._stop_requested = True
        self._stop_btn.configure(state="disabled")

    def _connect_browser(self, btn_text):
        """Connect to Edge via CDP on port 9222. Raises RuntimeError on failure."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", CDP_PORT)) != 0:
                import os
                edge_paths = [
                    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
                ]
                edge_exe = next((p for p in edge_paths if os.path.isfile(p)), "msedge.exe")
                raise RuntimeError(
                    f"Edge not listening on port {CDP_PORT}.\n"
                    "Launch Edge from PowerShell with:\n"
                    f'  & "{edge_exe}" --remote-debugging-port={CDP_PORT}'
                )

        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")

        contexts = self._browser.contexts
        if not contexts or not contexts[0].pages:
            raise RuntimeError("No pages open in Edge. Open the account activity page first.")

        # Search all tabs for one containing the button text
        for ctx in contexts:
            for page in ctx.pages:
                try:
                    loc = page.get_by_text(btn_text, exact=False).first
                    loc.wait_for(timeout=2000)
                    self._page = page
                    return self._page
                except Exception:
                    continue

        # Fallback to first page if button not found yet (let click_worker handle the timeout)
        self._page = contexts[0].pages[0]
        return self._page

    def _cleanup_browser(self):
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._playwright = None
        self._browser = None
        self._page = None

    def _extract_html(self, page):
        """Extract outerHTML of the PastTransactions tbody element in chunks to avoid truncation."""
        el = page.locator('tbody[data-bind="foreach: PastTransactions()"]')
        el.wait_for(timeout=10000)
        # Wait for row count to stabilize (DOM may still be rendering after last click)
        prev_count = 0
        for _ in range(30):  # up to 30 seconds
            row_count = el.evaluate("el => el.querySelectorAll('tr').length")
            if row_count == prev_count and row_count > 0:
                break
            prev_count = row_count
            time.sleep(1)
        self._queue.put(("info", f"Found {row_count} rows in DOM, extracting..."))
        chunk_size = 50
        parts = ['<tbody data-bind="foreach: PastTransactions()">']
        for start in range(0, row_count, chunk_size):
            end = min(start + chunk_size, row_count)
            chunk = el.evaluate("""el => {
                const rows = el.querySelectorAll('tr');
                let html = '';
                for (let i = %d; i < %d; i++) { html += rows[i].outerHTML; }
                return html;
            }""" % (start, end))
            parts.append(chunk)
        parts.append('</tbody>')
        return ''.join(parts)

    def _click_worker(self, btn_text, click_count, delay):
        try:
            self._queue.put(("info", "Connecting to Edge..."))
            page = self._connect_browser(btn_text)
            title = page.title()
            self._queue.put(("info", f"Connected to: {title}"))

            for i in range(1, click_count + 1):
                if self._stop_requested:
                    self._queue.put(("info", "Stopped by user."))
                    break

                self._queue.put(("info", f"Click {i} of {click_count}..."))
                try:
                    page.get_by_text(btn_text, exact=False).first.click(timeout=10000)
                    self._queue.put(("success", f"Click {i} of {click_count} completed."))
                except Exception as e:
                    self._queue.put(("error", f"Click {i} failed: {e}"))
                    self._queue.put(("info", "Button may have disappeared. Stopping."))
                    break

                # Wait in small chunks for responsive cancellation
                if i < click_count:
                    chunks = int(delay / 0.2)
                    for _ in range(chunks):
                        if self._stop_requested:
                            break
                        time.sleep(0.2)
                    remainder = delay - (chunks * 0.2)
                    if remainder > 0 and not self._stop_requested:
                        time.sleep(remainder)

            # Extract and save tbody HTML (skip if user stopped mid-run)
            if not self._stop_requested:
                try:
                    self._queue.put(("info", "Extracting transaction HTML..."))
                    html = self._extract_html(page)
                    import os
                    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Account_Activity_WBC.txt")
                    with open(out_path, "w", encoding="utf-8") as f:
                        f.write(html)
                    self._queue.put(("success", f"Saved HTML to Account_Activity_WBC.txt ({len(html)} bytes)"))
                    csv_path, row_count = self._convert_html_to_csv(html)
                    self._queue.put(("success", f"Saved CSV to Account_Activity_WBC.csv ({row_count} rows)"))
                except Exception as e:
                    self._queue.put(("error", f"HTML extraction failed: {e}"))

        except ImportError:
            self._queue.put(("error", "Playwright not installed. Run: pip install playwright"))
        except RuntimeError as e:
            self._queue.put(("error", str(e)))
        except Exception as e:
            self._queue.put(("error", f"Unexpected error: {e}"))
        finally:
            self._cleanup_browser()
            self._queue.put(("done", None))

    def _convert_html_to_csv(self, html):
        """Parse the tbody HTML and write Account_Activity_WBC.csv. Returns (csv_path, row_count)."""
        import os
        rows = re.findall(r'<tr data-bind="css:.*?</tr>', html, re.DOTALL)

        csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Account_Activity_WBC.csv")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Date", "Description", "Debit", "Credit", "Balance"])

            for row in rows:
                # Date: "3 <abbr title="March">Mar</abbr> 2026"
                date_m = re.search(r'displayDateOnly.*?">(\d+)\s*<abbr title="\w+">(\w+)</abbr>\s*(\d{4})', row, re.DOTALL)
                date = f"{date_m.group(1)}-{date_m.group(2)}-{date_m.group(3)}" if date_m else ""

                # Description
                desc_m = re.search(r'data-bind="text: Description">(.*?)</span>', row, re.DOTALL)
                desc = desc_m.group(1).strip() if desc_m else ""

                # Debit: inside <!-- ko 'if': IsDebit--> block
                debit = ""
                debit_m = re.search(r"IsDebit--><span[^>]*>(.*?)</span>", row)
                if debit_m:
                    val = debit_m.group(1).replace("$", "").replace(",", "").strip()
                    if val:
                        debit = val

                # Credit: inside <span data-bind="ifnot: IsDebit"> block
                credit = ""
                credit_m = re.search(r'ifnot: IsDebit"><span data-bind="html: Amount">(.*?)</span>', row)
                if credit_m:
                    val = credit_m.group(1).replace("$", "").replace(",", "").strip()
                    if val:
                        credit = val

                # Balance
                balance = ""
                bal_m = re.search(r'account-activity-runningbalance[^>]*>(.*?)</span>', row)
                if bal_m:
                    balance = bal_m.group(1).replace("$", "").replace(",", "").strip()

                writer.writerow([date, desc, debit, credit, balance])

        return csv_path, len(rows)

    def _check_queue(self):
        while not self._queue.empty():
            try:
                tag, message = self._queue.get_nowait()
            except queue.Empty:
                break

            if tag == "done":
                self._start_btn.configure(state="normal")
                self._stop_btn.configure(state="disabled")
            else:
                self._log(message, tag)

        self.root.after(50, self._check_queue)

    def _on_close(self):
        self._stop_requested = True
        self._cleanup_browser()
        self.root.destroy()


if __name__ == "__main__":
    App()
