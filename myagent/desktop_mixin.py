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

    def _resolve_coord_state(self, display=None):
        """Return (scale, (ox, oy), (iw, ih)) for the given display index, or current state.
        When display is given but not found in _display_states, returns the current state
        (so the caller can detect a missing screenshot via the (0, 0) dims check)."""
        if display is not None and display in self._display_states:
            return self._display_states[display]
        return (self._screenshot_scale, self._screenshot_offset, self._screenshot_dims)

    def _draw_coord_grid(self, img):
        """Overlay a faint coordinate grid every 100px with labels at intersections.
        Drawn on a copy of the image so the original is unchanged."""
        try:
            from PIL import ImageDraw, ImageFont
        except Exception:
            return img
        img = img.convert("RGB").copy()
        draw = ImageDraw.Draw(img)
        w, h = img.size
        grid_color = (255, 0, 255)   # magenta — uncommon in UIs, easy for vision models to see
        label_bg = (255, 255, 255)
        label_fg = (255, 0, 255)
        try:
            font = ImageFont.truetype("arial.ttf", 10)
        except Exception:
            try:
                font = ImageFont.truetype("DejaVuSans.ttf", 10)
            except Exception:
                font = ImageFont.load_default()
        spacing = 100
        for x in range(spacing, w, spacing):
            draw.line([(x, 0), (x, h)], fill=grid_color, width=1)
        for y in range(spacing, h, spacing):
            draw.line([(0, y), (w, y)], fill=grid_color, width=1)
        # Labels at intersections (skip 0,0 to avoid clutter at top-left)
        for x in range(0, w, spacing):
            for y in range(0, h, spacing):
                if x == 0 and y == 0:
                    continue
                label = f"{x},{y}"
                tx, ty = x + 3, y + 3
                try:
                    bbox = draw.textbbox((tx, ty), label, font=font)
                    draw.rectangle(bbox, fill=label_bg)
                except Exception:
                    pass
                draw.text((tx, ty), label, fill=label_fg, font=font)
        return img

    def _capture_single_display(self, display_idx, region=None, grid=False):
        """Capture a single display (or region), resize to API limit, update scale/offset.
        Returns list of content blocks [text, image] or error string."""
        # Diagnostic: log the full capture parameters up front (only when Diag checkbox is on)
        if self.diag_enabled.get():
            try:
                _diag_rects = self._get_display_rects()
                _diag_all_rects_str = ", ".join(
                    f"#{i}=({l},{t},{r-l}x{b-t})" for i, (l, t, r, b) in enumerate(_diag_rects)
                ) if _diag_rects else "(none)"
                self.queue.put({"type": "tool_info", "content": (
                    f"[DIAG capture] display_idx={display_idx} region={region} "
                    f"all_display_rects={_diag_all_rects_str}\n"
                )})
            except Exception as _diag_e:
                self.queue.put({"type": "tool_info", "content": f"[DIAG capture] error: {_diag_e}\n"})
        # Snapshot the active scale/offset BEFORE any state mutation so region→screen
        # conversion always uses the coordinate space the model was just looking at,
        # even when chaining region screenshots (region-inside-region).
        entry_scale = self._screenshot_scale
        entry_offset = self._screenshot_offset
        if region:
            ox, oy = entry_offset
            rx, ry, rw, rh = round(float(region[0])), round(float(region[1])), round(float(region[2])), round(float(region[3]))
            screen_x = round(rx * entry_scale) + ox
            screen_y = round(ry * entry_scale) + oy
            screen_w = max(round(rw * entry_scale), 1)
            screen_h = max(round(rh * entry_scale), 1)
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
            # Gemini supports much higher resolution than Anthropic via its tile system
            # (each image broken into 768x768 tiles, up to many tiles per request).
            # Bumping above the previous Anthropic-matched 1568/1.15MP cap gives older
            # models like Gemini 2.5 Pro substantially more pixel density on small UI
            # elements (close buttons, icons, menu items), which they otherwise struggle
            # to localize. 2048/4M is safe under Gemini's tile budget.
            max_long_edge, max_megapixels = 2048, 4_000_000
        elif self.provider == "OpenAI":
            # OpenAI's vision pipeline silently resizes images to ~2048 long edge
            # server-side, regardless of what we send. Verified empirically: when
            # we sent 2560x1440 the model's code_interpreter loaded the cached
            # bytes and PIL reported 2048x1152. Capping ourselves at 2048 ensures
            # _screenshot_scale matches what the model actually sees — otherwise
            # our scale=1.0 lies about the relationship between image pixels and
            # screen pixels, and any model that inspects dimensions via code
            # interpreter gets confused and pre-scales coordinates.
            # 5M megapixels is a permissive cap (well above 2048x2048=4.2MP) so
            # the long-edge constraint is the only one that ever bites.
            max_long_edge, max_megapixels = 2048, 5_000_000
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
        # Diagnostic: log the full pipeline result so we can see exactly what
        # dims/scale/offset the coordinate math will use (Diag checkbox only).
        if self.diag_enabled.get():
            try:
                self.queue.put({"type": "tool_info", "content": (
                    f"[DIAG capture] phys={phys_w}x{phys_h} logical={logical_w}x{logical_h} "
                    f"sent_to_model={img_w}x{img_h} scale={self._screenshot_scale:.4f} "
                    f"offset={self._screenshot_offset}\n"
                )})
            except Exception:
                pass
        # Cache raw pre-grid image bytes for find_element re-use (Gemini pointing tool)
        raw_buf = io.BytesIO()
        img.save(raw_buf, format="PNG")
        raw_bytes = raw_buf.getvalue()
        self._last_screenshot_bytes = raw_bytes
        # Optional grid overlay — drawn AFTER resize so labels match the image the model sees
        if grid:
            img = self._draw_coord_grid(img)
        # Track per-display coordinate state AND raw image bytes so the model can
        # disambiguate clicks AND find_element calls in multi-display setups.
        # Full-display captures populate BOTH "most recent" (_display_states /
        # _display_images) AND "full display" (_display_full_states / _display_full_images).
        # Region captures populate only "most recent" — the full state is preserved
        # so subsequent region screenshots compute their coordinates against the
        # actual full display image, not against a stacked previous region.
        if not region:
            self._display_states[display_idx] = (
                self._screenshot_scale, self._screenshot_offset, self._screenshot_dims,
            )
            self._display_images[display_idx] = raw_bytes
            self._display_full_states[display_idx] = (
                self._screenshot_scale, self._screenshot_offset, self._screenshot_dims,
            )
            self._display_full_images[display_idx] = raw_bytes
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
        return img_w, img_h, b64_data

    def do_screenshot(self, region=None, display=None, grid=False):
        try:
            grid_note = " (grid overlay enabled)" if grid else ""
            if region:
                # Region screenshot. The model's region coordinates are always
                # interpreted relative to the FULL display image space — not
                # relative to whatever was captured most recently. So we restore
                # the FULL display state from _display_full_states[N] before
                # computing the region origin. This is the key difference from
                # mouse_click, which uses the "most recent capture" state in
                # _display_states[N] (which may be a region).
                target_display = display if display is not None else 0
                if display is not None and display in self._display_full_states:
                    ds_scale, ds_offset, ds_dims = self._display_full_states[display]
                    self._screenshot_scale = ds_scale
                    self._screenshot_offset = ds_offset
                    self._screenshot_dims = ds_dims
                img_w, img_h, b64_data = self._capture_single_display(target_display, region=region, grid=grid)
                # Update the "most recent capture" state for this display so
                # subsequent mouse_click(display=N) interprets its coordinates in
                # the region image we just sent. _display_full_states[N] is left
                # alone — chained region captures keep using the full display
                # space for region coordinates.
                if display is not None:
                    self._display_states[display] = (
                        self._screenshot_scale,
                        self._screenshot_offset,
                        self._screenshot_dims,
                    )
                    if self._last_screenshot_bytes is not None:
                        self._display_images[display] = self._last_screenshot_bytes
                disp_hint = f", display={display}" if display is not None else ""
                return [
                    {"type": "text", "text": (
                        f"Region screenshot. The image you see is exactly {img_w}x{img_h} pixels.{grid_note} "
                        f"Use pixel coordinates AS YOU READ THEM from this image for mouse_click{disp_hint}. "
                        "Do NOT scale or convert coordinates to a different resolution — the system handles "
                        "scaling internally. Note: if you want another region screenshot, the x/y coordinates "
                        "for that next call should be relative to the FULL display image (not this region)."
                    )},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}},
                ]
            # Determine how many displays to capture
            rects = self._get_display_rects()
            num_displays = len(rects) if rects else 1
            if display is not None:
                # Specific display requested
                img_w, img_h, b64_data = self._capture_single_display(display, grid=grid)
                return [
                    {"type": "text", "text": (
                        f"Display {display} screenshot. The image you see is exactly {img_w}x{img_h} pixels.{grid_note} "
                        f"Use pixel coordinates AS YOU READ THEM from this image for mouse_click(display={display}). "
                        "Do NOT scale or convert coordinates to a different resolution — the system handles "
                        "scaling internally."
                    )},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}},
                ]
            # No display specified — capture ALL displays
            result = []
            for i in range(num_displays):
                img_w, img_h, b64_data = self._capture_single_display(i, grid=grid)
                result.append({"type": "text", "text": f"Display {i} ({img_w}x{img_h} pixels){grid_note}:"})
                result.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64_data}})
            result.append({"type": "text", "text": (
                "Use pixel coordinates AS YOU READ THEM from each image for mouse_click — do NOT scale or convert. "
                "When clicking, pass display=N to specify which display the coordinates are from. "
                "For precision on small targets, take a region screenshot of that display first."
            )})
            # Note: we no longer re-capture display 0 here. _display_states is
            # populated for every captured display in the loop above, so the
            # model can disambiguate via the `display` parameter on mouse_click.
            return result
        except Exception as e:
            return f"Screenshot error: {e}"

    def do_mouse_click(self, x, y, button="left", clicks=1, display=None):
        try:
            scale, (ox, oy), (iw, ih) = self._resolve_coord_state(display)
            # Diagnostic: log the full click math before any bounds/rounding (Diag checkbox only)
            if self.diag_enabled.get():
                try:
                    self.queue.put({"type": "tool_info", "content": (
                        f"[DIAG click] input=(x={x}, y={y}) display={display} "
                        f"state: dims={iw}x{ih} scale={scale:.4f} offset=({ox},{oy})\n"
                    )})
                except Exception:
                    pass
            if not (iw and ih):
                return ("Take a screenshot first — no screenshot has been captured "
                        "yet, so click coordinates cannot be mapped to the screen.")
            x, y = round(float(x)), round(float(y))
            # Tiered out-of-bounds handling: silent for tiny rounding (≤2px),
            # warn-and-clamp for moderate overflow, refuse for gross overflow.
            warning = ""
            dx = max(0, -x, x - (iw - 1))
            dy = max(0, -y, y - (ih - 1))
            if dx or dy:
                tol_pct = 0.02
                tol_x = max(2, int(iw * tol_pct))
                tol_y = max(2, int(ih * tol_pct))
                if dx > tol_x or dy > tol_y:
                    return (
                        f"Click coords ({x},{y}) are outside screenshot bounds {iw}x{ih} "
                        f"by ({dx},{dy})px. Re-take a screenshot, or take a region "
                        "screenshot zoomed into the target and use coordinates from that image."
                    )
                if dx > 2 or dy > 2:
                    warning = f" ⚠ coords ({x},{y}) outside screenshot {iw}x{ih} — clamped"
                x = max(0, min(x, iw - 1))
                y = max(0, min(y, ih - 1))
            screen_x = round(x * scale) + ox
            screen_y = round(y * scale) + oy
            # Diagnostic: log the computed screen coords just before pyautogui fires (Diag checkbox only)
            if self.diag_enabled.get():
                try:
                    self.queue.put({"type": "tool_info", "content": (
                        f"[DIAG click] computed: image_clamped=({x},{y}) "
                        f"→ screen=({screen_x},{screen_y}) via {x}*{scale:.4f}+{ox}, "
                        f"{y}*{scale:.4f}+{oy}\n"
                    )})
                except Exception:
                    pass
            pyautogui.click(screen_x, screen_y, button=button, clicks=clicks)
            time.sleep(0.05)  # let the click register before the next screenshot
            disp_note = f", display {display}" if display is not None else ""
            return (
                f"Clicked ({button}, {clicks}x) at screen ({screen_x}, {screen_y}) "
                f"[image ({x},{y}) of {iw}x{ih}, scale {scale:.2f}x, offset ({ox},{oy}){disp_note}]{warning}"
            )
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

    def do_mouse_scroll(self, clicks, x=None, y=None, display=None):
        try:
            scale, (ox, oy), (iw, ih) = self._resolve_coord_state(display)
            kwargs = {}
            if x is not None or y is not None:
                if not (iw and ih):
                    return ("Take a screenshot first — no screenshot has been captured "
                            "yet, so scroll coordinates cannot be mapped to the screen.")
                if x is not None:
                    kwargs["x"] = round(round(float(x)) * scale) + ox
                if y is not None:
                    kwargs["y"] = round(round(float(y)) * scale) + oy
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

    def do_read_screen_text(self, x, y, width, height, display=None):
        try:
            scale, (ox, oy), (iw, ih) = self._resolve_coord_state(display)
            if not (iw and ih):
                return ("Take a screenshot first — no screenshot has been captured "
                        "yet, so OCR region cannot be mapped to the screen.")
            sx = round(round(float(x)) * scale) + ox
            sy = round(round(float(y)) * scale) + oy
            sw = max(round(round(float(width)) * scale), 1)
            sh = max(round(round(float(height)) * scale), 1)
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

    def do_mouse_drag(self, start_x, start_y, end_x, end_y, duration=0.5, button="left", display=None):
        try:
            scale, (ox, oy), (iw, ih) = self._resolve_coord_state(display)
            if not (iw and ih):
                return ("Take a screenshot first — no screenshot has been captured "
                        "yet, so drag coordinates cannot be mapped to the screen.")
            sx = round(round(float(start_x)) * scale) + ox
            sy = round(round(float(start_y)) * scale) + oy
            ex = round(round(float(end_x)) * scale) + ox
            ey = round(round(float(end_y)) * scale) + oy
            pyautogui.moveTo(sx, sy, duration=0.1)
            pyautogui.mouseDown(button=button)
            pyautogui.moveTo(ex, ey, duration=duration)
            pyautogui.mouseUp(button=button)
            time.sleep(0.05)
            return (
                f"Dragged from ({start_x},{start_y}) to ({end_x},{end_y}) "
                f"with {button} button over {duration}s"
            )
        except Exception as e:
            return f"Mouse drag error: {e}"
