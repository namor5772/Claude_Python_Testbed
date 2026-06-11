import os
import subprocess
import time
import base64
import socket
import io
import json

from PIL import Image

from myagent.constants import IS_WINDOWS


class BrowserMixin:

    def _ensure_browser(self):
        if self._page is not None:
            try:
                self._page.title()
                return self._page
            except Exception:
                self._cleanup_browser()

        def _port_open():
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                return s.connect_ex(("127.0.0.1", 9222)) == 0

        if not _port_open():
            if IS_WINDOWS:
                edge_paths = [
                    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
                    os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                    os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
                ]
            else:
                edge_paths = [
                    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                    os.path.expanduser("~/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                ]
            edge_exe = None
            for p in edge_paths:
                if os.path.isfile(p):
                    edge_exe = p
                    break
            if not edge_exe:
                raise RuntimeError(
                    "No supported browser found. Install Brave Browser, Google Chrome, or Microsoft Edge."
                )
            if IS_WINDOWS:
                import tempfile
                debug_profile = os.path.join(tempfile.gettempdir(), "myagent_browser_debug")
            else:
                debug_profile = os.path.expanduser("~/Library/Application Support/MyAgent/browser_profile")
            os.makedirs(debug_profile, exist_ok=True)
            self._edge_process = subprocess.Popen(
                [edge_exe,
                 "--remote-debugging-port=9222",
                 "--no-first-run",
                 "--no-default-browser-check",
                 "--disable-features=Translate",
                 "--disable-popup-blocking",
                 f"--user-data-dir={debug_profile}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for _ in range(30):
                if _port_open():
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError(
                    "Edge launched but debug port 9222 did not open. "
                    "If Edge was already running without --remote-debugging-port, "
                    "close all Edge windows and try again."
                )

        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
        contexts = self._browser.contexts
        if contexts and contexts[0].pages:
            self._page = contexts[0].pages[0]
        else:
            ctx = contexts[0] if contexts else self._browser.new_context()
            self._page = ctx.new_page()
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

    def do_browser_open(self, url):
        try:
            page = self._ensure_browser()
            try:
                current_url = page.url or ""
            except Exception:
                current_url = ""
            is_blank = (
                current_url in ("", "about:blank")
                or current_url.startswith(("chrome://newtab", "edge://newtab"))
            )
            if not is_blank:
                self._page = page.context.new_page()
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return f"Navigated to {url} — page title: {self._page.title()}"
        except Exception as e:
            return f"Browser open error: {e}"

    def do_browser_navigate(self, url):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return f"Navigated to {url} — page title: {self._page.title()}"
        except Exception as e:
            return f"Browser navigate error: {e}"

    def do_browser_click(self, selector=None, text=None):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            if selector:
                self._page.click(selector, timeout=5000)
                return f"Clicked element: {selector}"
            if text:
                self._page.get_by_text(text, exact=False).first.click(timeout=5000)
                return f"Clicked element with text: {text}"
            return "Provide either 'selector' or 'text' to click."
        except Exception as e:
            return f"Browser click error: {e}"

    def do_browser_fill(self, selector, value):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            self._page.fill(selector, value, timeout=5000)
            return f"Filled {selector} with value ({len(value)} chars)"
        except Exception as e:
            return f"Browser fill error: {e}"

    def do_browser_get_text(self, selector=None):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            if selector:
                text = self._page.inner_text(selector, timeout=5000)
            else:
                text = self._page.inner_text("body", timeout=10000)
            if len(text) > 20000:
                text = text[:20000] + "\n\n[Content truncated at 20k chars]"
            return text
        except Exception as e:
            return f"Browser get text error: {e}"

    def do_browser_run_js(self, code):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            stripped = code.strip()
            if stripped.startswith("return "):
                code = f"() => {{ {stripped} }}"
            result = self._page.evaluate(code)
            return json.dumps(result, indent=2, default=str) if result is not None else "[No return value]"
        except Exception as e:
            return f"Browser JS error: {e}"

    def do_browser_screenshot(self):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            raw = self._page.screenshot(type="png")
            img = Image.open(io.BytesIO(raw))
            orig_w, orig_h = img.size
            max_w = 2048
            if orig_w > max_w:
                ratio = orig_w / max_w
                new_h = int(orig_h / ratio)
                img = img.resize((max_w, new_h))
                img_w, img_h = max_w, new_h
            else:
                img_w, img_h = orig_w, orig_h
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64_data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
            return [
                {"type": "text", "text": f"Browser screenshot ({img_w}x{img_h})."},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}},
            ]
        except Exception as e:
            return f"Browser screenshot error: {e}"

    def do_browser_close(self):
        try:
            self._cleanup_browser()
            return "Browser connection closed. Edge is still running."
        except Exception as e:
            return f"Browser close error: {e}"

    def do_browser_wait_for(self, selector, timeout=10000):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            el = self._page.wait_for_selector(selector, timeout=timeout)
            text = el.inner_text() if el else ""
            return f"Element '{selector}' appeared. Text: {text[:500]}"
        except Exception as e:
            return f"Browser wait error: {e}"

    def do_browser_select(self, selector, value=None, label=None):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            if value:
                self._page.select_option(selector, value=value, timeout=5000)
                return f"Selected option with value '{value}' in {selector}"
            if label:
                self._page.select_option(selector, label=label, timeout=5000)
                return f"Selected option with label '{label}' in {selector}"
            return "Provide either 'value' or 'label' to select."
        except Exception as e:
            return f"Browser select error: {e}"

    def do_browser_get_elements(self, selector, limit=10):
        try:
            if not self._page:
                return "No browser connected. Call browser_open first."
            js = f"""
            (() => {{
                const els = document.querySelectorAll({json.dumps(selector)});
                const results = [];
                for (let i = 0; i < Math.min(els.length, {limit}); i++) {{
                    const el = els[i];
                    const rect = el.getBoundingClientRect();
                    const attrs = {{}};
                    for (const a of el.attributes) attrs[a.name] = a.value;
                    results.push({{
                        tag: el.tagName.toLowerCase(),
                        text: el.innerText.substring(0, 200),
                        attrs: attrs,
                        visible: rect.width > 0 && rect.height > 0,
                        rect: {{top: Math.round(rect.top), left: Math.round(rect.left),
                                width: Math.round(rect.width), height: Math.round(rect.height)}}
                    }});
                }}
                return results;
            }})()
            """
            results = self._page.evaluate(js)
            if not results:
                return f"No elements found matching '{selector}'"
            lines = [f"Found {len(results)} element(s) matching '{selector}':"]
            lines.extend(
                f"  <{r['tag']}> text={r['text'][:80]!r}\n"
                f"      attrs: {r['attrs']}\n"
                f"      visible: {r['visible']}, rect: {r.get('rect', {})}"
                for r in results
            )
            return "\n".join(lines)
        except Exception as e:
            return f"browser_get_elements error: {e}"
