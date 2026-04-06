import os
import io
import csv
import base64
import time
import subprocess
import tkinter as tk

from myagent.constants import IS_WINDOWS, _HAS_DESKTOP, _SUBPROCESS_NOWND

if _HAS_DESKTOP:
    import pyautogui
    from PIL import Image, ImageGrab

if IS_WINDOWS:
    try:
        import pygetwindow as gw
    except Exception:
        pass


class DesktopMixin:

    def do_csv_search(self, file_path, search_value, column=None, match_mode="contains", max_results=50, delimiter=None):
        try:
            if not os.path.isfile(file_path):
                return f"Error: File not found: {file_path}"
            with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
                if delimiter is None:
                    sample = f.read(8192)
                    f.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=',\t|;')
                        delimiter = dialect.delimiter
                    except csv.Error:
                        delimiter = ','
                elif delimiter == '\\t':
                    delimiter = '\t'
                reader = csv.DictReader(f, delimiter=delimiter)
                headers = reader.fieldnames
                if not headers:
                    return "Error: CSV file has no headers."
                if column and column not in headers:
                    return f"Error: Column '{column}' not found. Available columns: {', '.join(headers)}"
                search_lower = search_value.lower()
                matches = []
                for row_num, row in enumerate(reader, start=2):
                    cells_to_check = [row.get(column, "")] if column else row.values()
                    for cell in cells_to_check:
                        cell_lower = (cell or "").lower()
                        if match_mode == "exact" and cell_lower == search_lower:
                            matched = True
                        elif match_mode == "starts_with" and cell_lower.startswith(search_lower):
                            matched = True
                        elif match_mode == "contains" and search_lower in cell_lower:
                            matched = True
                        else:
                            matched = False
                        if matched:
                            matches.append((row_num, row))
                            break
                    if len(matches) >= max_results:
                        break
            if not matches:
                scope = f"in column '{column}'" if column else "in any column"
                return f"No matches found for '{search_value}' {scope}.\nColumns: {', '.join(headers)}"
            lines = [f"Found {len(matches)} match(es). Columns: {', '.join(headers)}\n"]
            for row_num, row in matches:
                lines.append(f"--- Row {row_num} ---")
                for h in headers:
                    lines.append(f"  {h}: {row.get(h, '')}")
            if len(matches) >= max_results:
                lines.append(f"\n[Results limited to {max_results}. Use max_results to increase.]")
            output = "\n".join(lines)
            if len(output) > 20000:
                output = output[:20000] + "\n\n[Output truncated...]"
            return output
        except UnicodeDecodeError:
            return "Error: File encoding not supported. Expected UTF-8 CSV."
        except Exception as e:
            return f"Error reading CSV: {e}"

    # ── Desktop Automation Tools ────────────────────────────────────────

    KNOWN_APPS = {
        "chrome": "start chrome",
        "firefox": "start firefox",
        "edge": "start msedge",
        "notepad": "notepad",
        "notepad++": "start notepad++",
        "calculator": "calc",
        "calc": "calc",
        "excel": "start excel",
        "word": "start winword",
        "powerpoint": "start powerpnt",
        "explorer": "explorer",
        "cmd": "start cmd",
        "powershell": "start powershell",
        "vscode": "code",
        "code": "code",
        "spotify": "start spotify:",
        "discord": "start discord:",
        "slack": "start slack:",
        "teams": "start msteams:",
    } if IS_WINDOWS else {
        "chrome": "open -a 'Google Chrome'",
        "firefox": "open -a Firefox",
        "edge": "open -a 'Microsoft Edge'",
        "safari": "open -a Safari",
        "calculator": "open -a Calculator",
        "calc": "open -a Calculator",
        "terminal": "open -a Terminal",
        "finder": "open .",
        "explorer": "open .",
        "vscode": "code",
        "code": "code",
        "spotify": "open -a Spotify",
        "discord": "open -a Discord",
        "slack": "open -a Slack",
        "teams": "open -a 'Microsoft Teams'",
    }

    def _macos_display_screenshot(self, display_index=0):
        """Capture a single display on macOS using Quartz CoreGraphics.
        Returns (PIL Image, display_origin_x, display_origin_y) or (None, 0, 0)."""
        try:
            import Quartz
            rects = self._get_macos_display_rects()
            if not rects or display_index >= len(rects):
                return None, 0, 0
            # Use API order: display 0 = primary (origin 0,0)
            l, t, r, b = rects[display_index]
            w, h = r - l, b - t
            # Capture just this display's region
            cg_rect = Quartz.CGRectMake(l, t, w, h)
            cg_img = Quartz.CGWindowListCreateImage(
                cg_rect,
                Quartz.kCGWindowListOptionOnScreenOnly,
                Quartz.kCGNullWindowID,
                Quartz.kCGWindowImageDefault,
            )
            if not cg_img:
                return None, 0, 0
            iw = Quartz.CGImageGetWidth(cg_img)
            ih = Quartz.CGImageGetHeight(cg_img)
            cf_data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(cg_img))
            img = Image.frombytes("RGBA", (iw, ih), cf_data, "raw", "BGRA")
            return img.convert("RGB"), l, t
        except Exception:
            return None, 0, 0

    def _capture_single_display(self, display_idx, region=None):
        """Capture a single display (or region), resize to API limit, update scale/offset.
        Returns list of content blocks [text, image] or error string."""
        if region:
            # Convert image coordinates to screen coordinates using current scale/offset
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            rx, ry, rw, rh = int(region[0]), int(region[1]), int(region[2]), int(region[3])
            screen_x = round(rx * scale) + ox
            screen_y = round(ry * scale) + oy
            screen_w = max(round(rw * scale), 1)
            screen_h = max(round(rh * scale), 1)
            if IS_WINDOWS:
                # ImageGrab supports all_screens for multi-monitor; bbox is (l, t, r, b)
                img = ImageGrab.grab(bbox=(screen_x, screen_y,
                                          screen_x + screen_w, screen_y + screen_h),
                                    all_screens=True)
            else:
                img = pyautogui.screenshot(region=(screen_x, screen_y, screen_w, screen_h))
            # Update offset to region origin so subsequent clicks use region-relative coords
            self._screenshot_offset = (screen_x, screen_y)
            # DPI alignment: on macOS Retina, pyautogui returns physical resolution
            phys_w_r, phys_h_r = img.size
            if not IS_WINDOWS and phys_w_r != screen_w and screen_w:
                img = img.resize((screen_w, screen_h))
        elif not IS_WINDOWS:
            img, disp_x, disp_y = self._macos_display_screenshot(display_idx)
            if img is not None:
                self._screenshot_offset = (disp_x, disp_y)
            else:
                img = pyautogui.screenshot()
                self._screenshot_offset = (0, 0)
        else:
            # Windows: per-display capture via ImageGrab with all_screens
            rects = self._get_windows_display_rects()
            if rects and display_idx < len(rects):
                l, t, r, b = rects[display_idx]
                img = ImageGrab.grab(bbox=(l, t, r, b), all_screens=True)
                self._screenshot_offset = (l, t)
            else:
                img = pyautogui.screenshot()
                self._screenshot_offset = (0, 0)
        phys_w, phys_h = img.size
        # Align to logical coordinate space (handles DPI scaling)
        if not region:
            rects = self._get_display_rects()
            if rects:
                idx = min(display_idx, len(rects) - 1)
                l, t, r, b = rects[idx]
                log_w, log_h = r - l, b - t
            else:
                log_w, log_h = pyautogui.size()
            if phys_w != log_w and log_w:
                img = img.resize((log_w, log_h))
        logical_w, logical_h = img.size
        # Resize to API image limit (provider-specific)
        if self.provider == "Gemini":
            max_long_edge, max_megapixels = 1568, 1_150_000  # match Anthropic; prevents silent API resizing
        elif self.provider == "OpenAI":
            max_long_edge, max_megapixels = 2048, 2_000_000
        else:
            max_long_edge, max_megapixels = 1568, 1_150_000
        longest = max(logical_w, logical_h)
        if longest > max_long_edge:
            r = max_long_edge / longest
            max_w = round(logical_w * r)
        else:
            max_w = logical_w
        max_h = round(logical_h * (max_w / logical_w)) if logical_w else logical_h
        if max_w * max_h > max_megapixels:
            r = (max_megapixels / (max_w * max_h)) ** 0.5
            max_w = round(max_w * r)
        if logical_w > max_w:
            ratio = logical_w / max_w
            new_h = round(logical_h / ratio)
            img = img.resize((max_w, new_h))
            self._screenshot_scale = ratio
            img_w, img_h = max_w, new_h
        else:
            self._screenshot_scale = 1.0
            img_w, img_h = logical_w, logical_h
        self._screenshot_dims = (img_w, img_h)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
        return img_w, img_h, b64_data

    def do_screenshot(self, region=None, display=None):
        try:
            if region:
                # Region screenshot on the last-captured display
                img_w, img_h, b64_data = self._capture_single_display(0, region=region)
                return [
                    {"type": "text", "text": f"Region screenshot ({img_w}x{img_h}). Use pixel positions from this image for mouse_click."},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}},
                ]
            # Determine how many displays to capture
            rects = self._get_display_rects()
            num_displays = len(rects) if rects else 1
            if display is not None:
                # Specific display requested
                img_w, img_h, b64_data = self._capture_single_display(display)
                return [
                    {"type": "text", "text": f"Display {display} screenshot ({img_w}x{img_h}). Use pixel positions from this image for mouse_click — coordinates are automatically mapped to the correct screen."},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}},
                ]
            # No display specified — capture ALL displays
            result = []
            for i in range(num_displays):
                img_w, img_h, b64_data = self._capture_single_display(i)
                result.append({"type": "text", "text": f"Display {i} ({img_w}x{img_h}):"})
                result.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}})
            result.append({"type": "text", "text": "To click on a target, first call screenshot with that display number, THEN use mouse_click with coordinates from that specific display's screenshot."})
            # Set offset to primary display (display 0) as default for subsequent clicks
            if num_displays > 1:
                self._capture_single_display(0)  # reset scale/offset to display 0
            return result
        except Exception as e:
            return f"Screenshot error: {e}"

    def do_mouse_click(self, x, y, button="left", clicks=1):
        try:
            x, y = int(x), int(y)
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            iw, ih = self._screenshot_dims
            # Clamp to screenshot image bounds to prevent gross misclicks
            warning = ""
            if iw and ih and (x < 0 or y < 0 or x >= iw or y >= ih):
                warning = f" ⚠ coords ({x},{y}) outside screenshot {iw}x{ih} — clamped"
                x = max(0, min(x, iw - 1))
                y = max(0, min(y, ih - 1))
            screen_x = round(x * scale) + ox
            screen_y = round(y * scale) + oy
            pyautogui.click(screen_x, screen_y, button=button, clicks=clicks)
            return f"Clicked ({button}, {clicks}x) at screen ({screen_x}, {screen_y}) [image ({x},{y}) of {iw}x{ih}, scale {scale:.2f}x, offset ({ox},{oy})]{warning}"
        except Exception as e:
            return f"Click error: {e}"

    def do_type_text(self, text, interval=0.02):
        try:
            if all(ord(c) < 128 for c in text):
                pyautogui.write(text, interval=interval)
            else:
                try:
                    import pyperclip
                except ImportError:
                    return "Error: pyperclip is not installed (needed for non-ASCII text). Install with: pip install pyperclip"
                pyperclip.copy(text)
                paste_mod = "ctrl" if IS_WINDOWS else "command"
                pyautogui.hotkey(paste_mod, "v")
            return f"Typed {len(text)} characters"
        except Exception as e:
            return f"Type error: {e}"

    def do_press_key(self, keys):
        try:
            parts = [k.strip().lower() for k in keys.split("+")]
            # Normalize common aliases (platform-adaptive)
            if IS_WINDOWS:
                aliases = {"windows": "win", "control": "ctrl", "return": "enter", "esc": "escape"}
            else:
                aliases = {"windows": "command", "win": "command", "cmd": "command",
                           "control": "ctrl", "return": "enter", "esc": "escape", "option": "alt"}
            parts = [aliases.get(p, p) for p in parts]
            if len(parts) == 1:
                pyautogui.press(parts[0])
            else:
                pyautogui.hotkey(*parts)
            return f"Pressed: {keys}"
        except Exception as e:
            return f"Key press error: {e}"

    def do_mouse_scroll(self, clicks, x=None, y=None):
        try:
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            kwargs = {}
            if x is not None:
                kwargs["x"] = round(int(x) * scale) + ox
            if y is not None:
                kwargs["y"] = round(int(y) * scale) + oy
            pyautogui.scroll(clicks, **kwargs)
            direction = "up" if clicks > 0 else "down"
            pos = f" at ({x}, {y})" if x is not None else ""
            return f"Scrolled {direction} {abs(clicks)} clicks{pos}"
        except Exception as e:
            return f"Scroll error: {e}"

    def do_open_application(self, name, args=None):
        try:
            key = name.lower().strip()
            if key in self.KNOWN_APPS:
                cmd = self.KNOWN_APPS[key]
            else:
                cmd = name
            if args:
                subprocess.Popen([cmd, args], **_SUBPROCESS_NOWND)
            else:
                subprocess.Popen(cmd, shell=True, **_SUBPROCESS_NOWND)
            return f"Opened {name}{f' with {args}' if args else ''} (command: {cmd})"
        except Exception as e:
            return f"Error opening {name}: {e}"

    def _find_windows_by_title(self, title):
        """Find windows matching title (case-insensitive substring). Cross-platform."""
        pattern = title.lower()
        if IS_WINDOWS:
            matches = gw.getWindowsWithTitle(title)
            return [{"title": w.title, "left": w.left, "top": w.top,
                      "width": w.width, "height": w.height, "_win": w} for w in matches]
        else:
            import Quartz
            wins = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID)
            results = []
            for w in wins:
                app = w.get("kCGWindowOwnerName", "")
                name = w.get("kCGWindowName", "")
                full_title = f"{app} — {name}" if name else app
                if pattern in full_title.lower() or pattern in app.lower() or pattern in (name or "").lower():
                    b = w.get("kCGWindowBounds", {})
                    results.append({"title": full_title,
                                    "left": int(b.get("X", 0)), "top": int(b.get("Y", 0)),
                                    "width": int(b.get("Width", 0)), "height": int(b.get("Height", 0)),
                                    "_app": app, "_pid": w.get("kCGWindowOwnerPID")})
            return results

    def do_find_window(self, title, activate=False):
        try:
            windows = self._find_windows_by_title(title)
            if not windows:
                return f"No windows found matching '{title}'"
            results = []
            for w in windows:
                results.append(f"  Title: {w['title']}\n  Position: ({w['left']}, {w['top']})\n  Size: {w['width']}x{w['height']}")
            if activate and windows:
                try:
                    win = windows[0]
                    if IS_WINDOWS:
                        obj = win["_win"]
                        if obj.isMinimized:
                            obj.restore()
                        obj.activate()
                    else:
                        subprocess.run(["osascript", "-e",
                                        f'tell application "{win["_app"]}" to activate'],
                                       capture_output=True, timeout=5)
                    results.insert(0, f"Activated: {win['title']}")
                except Exception as e:
                    results.insert(0, f"Found but could not activate: {e}")
            return f"Found {len(windows)} window(s):\n" + "\n---\n".join(results)
        except Exception as e:
            return f"Window search error: {e}"

    def do_clipboard_read(self):
        try:
            text = self.root.clipboard_get()
            return f"Clipboard contents:\n{text}"
        except tk.TclError:
            return "Clipboard is empty or contains non-text data."
        except Exception as e:
            return f"Clipboard read error: {e}"

    def do_clipboard_write(self, text):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            preview = text[:100] + "..." if len(text) > 100 else text
            return f"Copied to clipboard ({len(text)} chars): {preview}"
        except Exception as e:
            return f"Clipboard write error: {e}"

    def do_wait_for_window(self, title, timeout=10):
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                windows = self._find_windows_by_title(title)
                if windows:
                    w = windows[0]
                    return (
                        f"Window found: {w['title']}\n"
                        f"Position: ({w['left']}, {w['top']})\n"
                        f"Size: {w['width']}x{w['height']}"
                    )
                time.sleep(0.5)
            return f"Timed out after {timeout}s waiting for window '{title}'"
        except Exception as e:
            return f"Wait for window error: {e}"

    def do_read_screen_text(self, x, y, width, height):
        try:
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            sx = round(int(x) * scale) + ox
            sy = round(int(y) * scale) + oy
            sw = max(round(int(width) * scale), 1)
            sh = max(round(int(height) * scale), 1)
            if IS_WINDOWS:
                img = ImageGrab.grab(bbox=(sx, sy, sx + sw, sy + sh), all_screens=True)
            else:
                img = pyautogui.screenshot(region=(sx, sy, sw, sh))

            if IS_WINDOWS:
                import winocr
                import asyncio
                result = asyncio.run(winocr.recognize_pil(img, lang="en"))
                text = result.text.strip()
            else:
                import objc, Quartz
                Vision = objc.loadBundle("Vision", bundle_path="/System/Library/Frameworks/Vision.framework",
                                         module_globals={})
                from Quartz import CGImageDestinationCreateWithData, CGImageDestinationAddImage
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                ns_data = Quartz.NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
                ci_image = Quartz.CIImage.imageWithData_(ns_data)
                handler = Quartz.VNImageRequestHandler.alloc().initWithCIImage_options_(ci_image, None)
                request = Quartz.VNRecognizeTextRequest.alloc().init()
                request.setRecognitionLevel_(0)  # 0 = accurate
                handler.performRequests_error_([request], None)
                observations = request.results()
                lines = []
                for obs in (observations or []):
                    candidates = obs.topCandidates_(1)
                    if candidates:
                        lines.append(candidates[0].string())
                text = "\n".join(lines).strip()

            if not text:
                return "OCR returned no text for the specified region."
            return f"OCR text from ({x},{y} {width}x{height}):\n{text}"
        except Exception as e:
            return f"OCR error: {e}"

    def do_find_image_on_screen(self, image_path, confidence=0.8):
        try:
            if not os.path.isfile(image_path):
                return f"Image file not found: {image_path}"
            location = pyautogui.locateOnScreen(image_path, confidence=confidence)
            if location is None:
                return f"Image not found on screen (confidence={confidence}): {image_path}"
            cx = location.left + location.width // 2
            cy = location.top + location.height // 2
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            img_cx = round((cx - ox) / scale) if scale else cx
            img_cy = round((cy - oy) / scale) if scale else cy
            return (
                f"Image found at region ({location.left}, {location.top}, "
                f"{location.width}x{location.height})\n"
                f"Center (screen coords): ({cx}, {cy})\n"
                f"Center (image coords for clicking): ({img_cx}, {img_cy})"
            )
        except Exception as e:
            return f"Find image error: {e}"

    def do_mouse_drag(self, start_x, start_y, end_x, end_y, duration=0.5, button="left"):
        try:
            scale = self._screenshot_scale
            ox, oy = self._screenshot_offset
            sx = round(int(start_x) * scale) + ox
            sy = round(int(start_y) * scale) + oy
            ex = round(int(end_x) * scale) + ox
            ey = round(int(end_y) * scale) + oy
            pyautogui.moveTo(sx, sy, duration=0.1)
            pyautogui.mouseDown(button=button)
            pyautogui.moveTo(ex, ey, duration=duration)
            pyautogui.mouseUp(button=button)
            return (
                f"Dragged from ({start_x},{start_y}) to ({end_x},{end_y}) "
                f"with {button} button over {duration}s"
            )
        except Exception as e:
            return f"Mouse drag error: {e}"
