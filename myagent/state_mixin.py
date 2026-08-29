import os, re, json, time, subprocess, ctypes, tkinter as tk
from tkinter import messagebox
from myagent.constants import IS_WINDOWS, AGENT_LOCK_PREFIX, DEFAULT_GEOMETRY, _BASE_DIR
from myagent.helpers import rotate_log_if_needed

# Optional diagnostic trace of every geometry save/restore, one line per event,
# to geometry_debug.log beside the state file (100 KB one-slot rotation). OFF by
# default; set MYAGENT_GEOMETRY_DEBUG=1 to turn it on — the reproduction switch
# for a "window comes back on the wrong monitor" report (it records the entry
# restored at launch and the geometry saved at each close, which is exactly what
# distinguishes a bad-save from a bad-restore). Best-effort, never fatal.
GEOMETRY_DEBUG = os.environ.get("MYAGENT_GEOMETRY_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
GEOMETRY_LOG_FILE = os.path.join(_BASE_DIR, "geometry_debug.log")
GEOMETRY_LOG_MAX_BYTES = 100_000

# Windows whose size/position persist per monitor layout, per instance
# (agent_state.json / agent_state_N.json): kind → key inside a layout entry.
# The on-disk key names predate this table and are kept so existing state
# files keep restoring unchanged.
GEOMETRY_KINDS = {
    "main": "geometry",
    "editor": "editor_geometry",
    "ps_safety": "ps_safety_dialog_geometry",
    "prompt": "prompt_dialog_geometry",
    "confirm": "confirm_dialog_geometry",
    "skills": "skills_dialog_geometry",
}
# A saved position is usable only if enough of the window's title bar lands on
# a real monitor to grab it: at least GEOMETRY_VISIBLE_W px wide, and at least
# GEOMETRY_TITLE_VISIBLE_H px of the window's top GEOMETRY_TITLE_STRIP px.
GEOMETRY_VISIBLE_W = 50
GEOMETRY_TITLE_STRIP = 30
GEOMETRY_TITLE_VISIBLE_H = 10


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
                # Judged stale — remove it under a per-slot mutex dir.
                # A bare check-then-remove is itself a TOCTOU: os.remove is
                # path-based and can't tell inode generations apart, so a
                # reclaimer descheduled between its re-read and its remove
                # would delete a sibling's FRESH replacement lock and both
                # would own the slot (the storm test hits this reliably on
                # macOS). The mutex serializes judge→remove; creation stays
                # arbitrated by O_EXCL below.
                if not self._reclaim_stale_lock(lock_path, stale_content):
                    continue  # a sibling owns or is reclaiming it
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

    @staticmethod
    def _reclaim_stale_lock(lock_path, judged_content):
        """Remove a judged-stale lock atomically w.r.t. sibling reclaimers.

        Returns True when the slot is free to O_EXCL-create (we removed the
        stale lock, or it was already gone), False when the slot should be
        skipped (a sibling owns it, is mid-reclaim, or the state is unclear).

        The mutex is a directory (os.mkdir is atomic on POSIX and Windows);
        the lock content is re-validated INSIDE the mutex so only the exact
        file generation that was judged stale can be removed. A crashed
        reclaimer's leftover mutex (the mkdir→rmdir window is a few
        syscalls) is cleared once it is older than 10s; this claimant still
        skips the slot and retries on a later pass/launch."""
        mutex = lock_path + ".reclaim"
        try:
            os.mkdir(mutex)
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(mutex) > 10:
                    os.rmdir(mutex)
            except OSError:
                pass
            return False
        except OSError:
            return False
        try:
            try:
                with open(lock_path) as f:
                    current = f.read().strip()
            except FileNotFoundError:
                return True  # already cleared — race for the empty slot
            except OSError:
                return False  # unreadable now — leave it for a later pass
            if judged_content is None or current != judged_content:
                return False  # changed hands — a sibling owns it now
            try:
                os.remove(lock_path)
            except FileNotFoundError:
                pass
            except OSError:
                return False
            return True
        finally:
            try:
                os.rmdir(mutex)
            except OSError:
                pass

    def _release_instance_lock(self):
        """Remove this instance's lock file."""
        lock_path = getattr(self, '_lock_path', None)
        if lock_path and os.path.exists(lock_path):
            try:
                os.remove(lock_path)
            except OSError:
                pass

    # ── Display Geometry ───────────────────────────────────────────────

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
        Primary monitor (origin 0,0) is always first. Physical pixels under the
        PER_MONITOR_AWARE_V2 context MyAgent.py sets before Tk loads — the same
        units Tk's `wm geometry` reports, so saved positions compare directly."""
        try:
            user32 = ctypes.windll.user32

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            monitors = []
            # BOOL CALLBACK MonitorEnumProc(HMONITOR, HDC, LPRECT, LPARAM) —
            # the handles and LPARAM are pointer-sized on x64
            MONITORENUMPROC = ctypes.WINFUNCTYPE(
                ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                ctypes.POINTER(RECT), ctypes.c_void_p)

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
    def _monitor_rects():
        """Every monitor's rect, falling back to the virtual desktop as one rect."""
        rects = StateMixin._get_display_rects()
        if rects:
            return list(rects)
        vx, vy, vw, vh = StateMixin._get_virtual_screen_bounds()
        return [(vx, vy, vx + vw, vy + vh)]

    @staticmethod
    def _primary_rect():
        """The monitor containing the origin (the primary), else the first one."""
        rects = StateMixin._monitor_rects()
        for rect in rects:
            left, top, right, bottom = rect
            if left <= 0 < right and top <= 0 < bottom:
                return rect
        return rects[0]

    @staticmethod
    def _get_monitor_config_key():
        """Return a string key identifying the current monitor layout, e.g.
        '-2560,0,0,1440|0,0,2560,1440' — every monitor's rect (Win32
        EnumDisplayMonitors / macOS CoreGraphics), sorted. Docked vs undocked,
        a rearrangement, or a monitor that has dropped off the bus each
        produce a different key, so every layout keeps its own set of window
        positions."""
        rects = StateMixin._get_display_rects()
        if rects:
            return "|".join(",".join(str(v) for v in rect) for rect in sorted(rects))
        # Fallback: use virtual screen bounds
        vx, vy, vw, vh = StateMixin._get_virtual_screen_bounds()
        return f"{vx},{vy},{vx + vw},{vy + vh}"

    @staticmethod
    def _parse_geometry(geo):
        """'WxH+X+Y' → (w, h, x, y), else None. X/Y may be negative — '+-723'
        is how Tk reports a window left of (or above) the primary monitor. A
        size-only string (DEFAULT_GEOMETRY) or Tk's right/bottom-anchored
        '-X' form, which this app never writes, is not a restorable position."""
        m = re.fullmatch(r"=?(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", str(geo or "").strip())
        if not m:
            return None
        return tuple(int(g) for g in m.groups())

    @staticmethod
    def _geometry_visible(x, y, w, h):
        """True when enough of the window's title bar lies on SOME monitor to
        grab it. Checked per monitor rather than against the virtual desktop's
        bounding box — an L-shaped layout has dead corners inside that box."""
        for left, top, right, bottom in StateMixin._monitor_rects():
            overlap_w = min(x + w, right) - max(x, left)
            overlap_h = min(y + GEOMETRY_TITLE_STRIP, bottom) - max(y, top)
            if overlap_w >= GEOMETRY_VISIBLE_W and overlap_h >= GEOMETRY_TITLE_VISIBLE_H:
                return True
        return False

    @staticmethod
    def _sanitize_geometry(geo, min_w=200, min_h=150):
        """Return `geo` normalised if it is a usable position on the CURRENT
        layout, else None — the caller then applies its own default (each
        dialog has its own; the main window uses DEFAULT_GEOMETRY). Rejects
        tiny windows and positions whose title bar no monitor shows."""
        parsed = StateMixin._parse_geometry(geo)
        if parsed is None:
            return None
        w, h, x, y = parsed
        if w < min_w or h < min_h:
            return None
        if not StateMixin._geometry_visible(x, y, w, h):
            return None
        return f"{w}x{h}+{x}+{y}"

    @staticmethod
    def _clamp_to_monitor(x, y, w, h):
        """Shift (x, y) so a w×h window sits inside the monitor containing that
        point (the primary if none does)."""
        home = StateMixin._primary_rect()
        for rect in StateMixin._monitor_rects():
            left, top, right, bottom = rect
            if left <= x < right and top <= y < bottom:
                home = rect
                break
        left, top, right, bottom = home
        x = max(left, min(x, right - w))
        y = max(top, min(y, bottom - h))
        return x, y

    # ── Window Geometry Persistence (main window + every dialog) ───────
    #
    # One mechanism for all six persisted windows: `_remember_geometry`
    # captures a window's REAL geometry into a per-process cache,
    # `_place_window` positions a dialog from that cache (or its default),
    # and `_save_last_state` / `_load_last_state` move the cache to and
    # from this instance's state file under the current monitor-layout key.

    def _geo_cache(self):
        """kind → last known NORMAL-state geometry (current layout)."""
        cache = getattr(self, "_geometry_cache", None)
        if cache is None:
            cache = self._geometry_cache = {}
        return cache

    def _remember_geometry(self, kind, win):
        """Cache `win`'s current geometry for `kind` — but only a REAL one: the
        window must be mapped and in the normal state.

        Why the guards (all three bit on Windows):
        - a never-mapped root reports '1x1+X+Y' until Tk's first idle pass, so
          the save in __init__ used to write a junk size that the next launch
          rejected as too small — and the window fell back to the primary
          monitor;
        - a maximized window reports the ZOOMED size with the NORMAL position
          ('2560x1417+-723+73'), a chimera that restored as a giant
          un-maximized window straddling both monitors — instead the zoomed
          flag is recorded and the last normal geometry kept;
        - an iconified window still holds its normal geometry but is left
          alone, so closing minimized-from-maximized still restores maximized.
        Returns the cached geometry, or None when nothing was captured."""
        try:
            if not win.winfo_exists():
                return None
            state = win.state()
        except tk.TclError:
            return None
        if kind == "main" and state in ("normal", "zoomed"):
            self._main_zoomed = (state == "zoomed")
        try:
            if state != "normal" or not win.winfo_ismapped():
                return None
            geo = win.geometry()
        except tk.TclError:
            return None
        parsed = self._parse_geometry(geo)
        if parsed is None or parsed[0] <= 1 or parsed[1] <= 1:
            return None
        self._geo_cache()[kind] = geo
        self._geometry_dirty = True
        return geo

    def _saved_geometry(self, kind, min_w=200, min_h=150):
        """The cached geometry for `kind`, if still visible on the current layout."""
        geo = self._geo_cache().get(kind)
        return self._sanitize_geometry(geo, min_w, min_h) if geo else None

    def _place_window(self, win, kind, default_size, parent=None, min_size=(200, 150)):
        """Position a dialog before it is shown: the saved geometry for `kind`
        when still visible on this layout, else `default_size` (shrunk to fit
        the monitor) centred on `parent` — the main window by default — and
        clamped onto that monitor. A withdrawn parent (--headless) centres the
        dialog on the primary monitor instead. Returns the geometry applied
        (the PS Safety dialog re-applies it after mapping)."""
        geo = self._saved_geometry(kind, *min_size)
        if geo is None:
            w, h = default_size
            parent = parent if parent is not None else self.root
            try:
                mapped = bool(parent.winfo_ismapped())
                px, py = parent.winfo_x(), parent.winfo_y()
                pw, ph = parent.winfo_width(), parent.winfo_height()
            except tk.TclError:
                mapped = False
            left, top, right, bottom = self._primary_rect()
            if mapped:
                cx, cy = px + pw // 2, py + ph // 2
            else:
                cx, cy = (left + right) // 2, (top + bottom) // 2
            for rect in self._monitor_rects():
                if rect[0] <= cx < rect[2] and rect[1] <= cy < rect[3]:
                    left, top, right, bottom = rect
                    break
            w, h = min(w, right - left), min(h, bottom - top)
            x, y = self._clamp_to_monitor(cx - w // 2, cy - h // 2, w, h)
            geo = f"{w}x{h}+{x}+{y}"
        win.geometry(geo)
        return geo

    def _open_geometry_windows(self):
        """(kind, window) for every persisted window currently open."""
        pairs = (("main", getattr(self, "root", None)),
                 ("editor", getattr(self, "instruction_editor_window", None)),
                 ("skills", getattr(self, "skills_editor_window", None)),
                 ("ps_safety", getattr(self, "_ps_safety_dialog", None)),
                 ("prompt", getattr(self, "_prompt_dialog", None)),
                 ("confirm", getattr(self, "_confirm_dialog", None)))
        out = []
        for kind, win in pairs:
            try:
                if win is not None and win.winfo_exists():
                    out.append((kind, win))
            except tk.TclError:
                pass
        return out

    def _build_geometry_entry(self):
        """Snapshot of every window's geometry for the current layout: open
        windows are re-captured live, closed ones contribute their cached
        last-known geometry. Empty when nothing real was ever captured."""
        for kind, win in self._open_geometry_windows():
            self._remember_geometry(kind, win)
        cache = self._geo_cache()
        entry = {key: cache[kind] for kind, key in GEOMETRY_KINDS.items() if cache.get(kind)}
        if entry:
            entry["main_zoomed"] = bool(getattr(self, "_main_zoomed", False))
            entry["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return entry

    def _geometries_for_save(self, existing_geometries, config_key):
        """The `geometries` dict to write. Other layouts' entries are preserved;
        this layout's entry is replaced only once a real geometry has been
        captured in this process (`_geometry_dirty`): a --headless run whose
        window never showed, or a launch still unmapped, must not overwrite a
        good entry with stale loaded values — possibly under a different key,
        if a monitor came or went during the run."""
        all_geos = dict(existing_geometries) if isinstance(existing_geometries, dict) else {}
        if getattr(self, "_geometry_dirty", False):
            entry = self._build_geometry_entry()
            if entry:
                all_geos[config_key] = entry
                self._geo_log("save", geometry=entry.get("geometry"),
                              zoomed=entry.get("main_zoomed"))
            else:
                self._geo_log("save-skip", why="no-real-geometry-captured")
        else:
            self._geo_log("save-skip", why="not-dirty",
                          existing=(all_geos.get(config_key) or {}).get("geometry"))
        return all_geos

    def _select_geometry_entry(self, state, config_key):
        """The layout entry to restore: this layout's own, else the legacy flat
        fields, else the most recently saved layout whose main-window position
        is still visible here — so a monitor that dropped off the bus, or a
        first launch after rearranging, keeps the window where it was rather
        than resetting it to the primary monitor."""
        all_geos = state.get("geometries")
        if not isinstance(all_geos, dict):
            all_geos = {}
        entry = all_geos.get(config_key)
        if isinstance(entry, dict):
            return entry
        if "geometry" in state:  # pre-per-layout state file
            return {k: state[k] for k in GEOMETRY_KINDS.values() if k in state}
        by_recency = sorted(
            (e for e in all_geos.values() if isinstance(e, dict)),
            key=lambda e: str(e.get("saved_at", "")), reverse=True)
        for entry in by_recency:
            if self._sanitize_geometry(entry.get("geometry")):
                return entry
        return {}

    def _default_main_geometry(self):
        """DEFAULT_GEOMETRY explicitly centred on the primary monitor. Without
        the +x+y the WM chooses the spot, which is NOT reliably the primary on
        a multi-monitor box — the same reason every dialog computes its own
        fallback position rather than leaving it to the WM."""
        parsed = self._parse_geometry(DEFAULT_GEOMETRY)
        if parsed:
            w, h = parsed[0], parsed[1]
        else:
            w, h = 1050, 930
        left, top, right, bottom = self._primary_rect()
        w, h = min(w, right - left), min(h, bottom - top)
        x, y = self._clamp_to_monitor(
            left + (right - left - w) // 2, top + (bottom - top - h) // 2, w, h)
        return f"{w}x{h}+{x}+{y}"

    def _apply_geometry_entry(self, entry):
        """Restore a layout entry: cache every window's geometry (dialogs are
        re-validated when they open) and place the main window — its saved
        position when visible, else centred on the primary monitor — re-
        maximized if it was closed maximized."""
        self._geo_log("restore-entry", geometry=entry.get("geometry"),
                      zoomed=entry.get("main_zoomed"), keys=",".join(sorted(entry)))
        cache = self._geo_cache()
        for kind, key in GEOMETRY_KINDS.items():
            geo = entry.get(key)
            if self._parse_geometry(geo):
                cache[kind] = geo
        main = cache.get("main")
        zoomed = entry.get("main_zoomed")
        if zoomed is None and main:
            # Entry written before the zoomed flag existed: a width equal to a
            # monitor's is the old code's maximized chimera (zoomed size +
            # normal position) — restore it maximized, at the default size.
            w, h, x, y = self._parse_geometry(main)
            if any(w == rect[2] - rect[0] for rect in self._get_display_rects()):
                main = f"{DEFAULT_GEOMETRY.split('+')[0]}+{x}+{y}"
                zoomed = True
                self._geometry_dirty = True  # heal the file on the first save
        geo = self._sanitize_geometry(main) if main else None
        if geo:
            cache["main"] = geo
            self.root.geometry(geo)
        else:
            cache.pop("main", None)
            geo = self._default_main_geometry()
            self.root.geometry(geo)
            self._geo_log("restore-default", applied=geo, rejected=main)
        self._main_zoomed = bool(zoomed)
        if self._main_zoomed:
            try:
                self.root.state("zoomed")
            except tk.TclError:
                self._main_zoomed = False
        # Remember where we placed the window so _reassert_main_geometry can
        # reclaim it if an external window manager (PowerToys FancyZones'
        # "open on active monitor" / "move to last zone", DisplayFusion, …)
        # yanks it to another monitor the moment it maps — the reason the main
        # window drifted while the dialogs (which re-assert on show) did not.
        self._reassert_geo = None if self._main_zoomed else geo
        self._geo_log("restore-applied", applied=geo, zoomed=self._main_zoomed)

    def _monitor_index_of_center(self, x, y, w, h):
        """Index (into _monitor_rects()) of the monitor holding the window's
        centre, or -1 if it is off every monitor."""
        cx, cy = x + w // 2, y + h // 2
        for i, (left, top, right, bottom) in enumerate(self._monitor_rects()):
            if left <= cx < right and top <= cy < bottom:
                return i
        return -1

    # Seconds after launch during which the restored monitor is defended
    # against an external mover; long enough to cover FancyZones acting on the
    # map event, short enough never to fight a deliberate move.
    REASSERT_WINDOW_SECS = 1.5

    def _start_geometry_reassert(self):
        """Arm the post-launch monitor-reclaim: open a short time window during
        which `_maybe_reclaim_monitor` (driven by the root's <Configure> and a
        few scheduled fallbacks) pulls the window back if an external mover
        drifts it to another monitor. No-op for a maximized restore or when no
        specific position was restored. Scheduled from __init__ after
        _load_last_state."""
        if not getattr(self, "_reassert_geo", None):
            return
        self._reassert_until = time.monotonic() + self.REASSERT_WINDOW_SECS
        # <Configure> catches the drift the instant it happens; these fallbacks
        # cover the case where the mover suppresses/coalesces that event.
        for delay in (150, 400, 800, 1200, 1500):
            self.root.after(delay, self._maybe_reclaim_monitor)

    def _maybe_reclaim_monitor(self):
        """Within the arm window, reclaim the restored spot ONLY when the window
        has drifted to a DIFFERENT monitor than it was restored to — the
        external-mover (FancyZones "open on active monitor") signature. A
        same-monitor nudge is left alone, and after the window closes it does
        nothing, so this never fights a deliberate move."""
        geo = getattr(self, "_reassert_geo", None)
        if not geo or getattr(self, "_main_zoomed", False):
            return
        if time.monotonic() > getattr(self, "_reassert_until", 0.0):
            return
        try:
            if not self.root.winfo_exists() or self.root.state() != "normal":
                return
            cur = self._parse_geometry(self.root.geometry())
            tgt = self._parse_geometry(geo)
            if not (cur and tgt):
                return
            if self._monitor_index_of_center(*cur) != self._monitor_index_of_center(*tgt):
                self.root.geometry(geo)
                self._geo_log("reassert", target=geo, drifted_from=f"{cur[2]}+{cur[3]}")
        except tk.TclError:
            pass

    def _geo_log(self, event, **fields):
        """Append one diagnostic line about a geometry save/restore. No-op
        unless MYAGENT_GEOMETRY_DEBUG is set — see GEOMETRY_DEBUG."""
        if not GEOMETRY_DEBUG:
            return
        try:
            rotate_log_if_needed(GEOMETRY_LOG_FILE, GEOMETRY_LOG_MAX_BYTES)
            inst = getattr(self, "_instance_num", "?")
            try:
                live = f"{self.root.state()}:{self.root.geometry()}" if self.root.winfo_exists() else "-"
            except tk.TclError:
                live = "-"
            parts = [time.strftime("%Y-%m-%d %H:%M:%S"), f"inst{inst}", event,
                     f"key={self._get_monitor_config_key()}", f"live={live}"]
            parts += [f"{k}={v}" for k, v in fields.items()]
            with open(GEOMETRY_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(" | ".join(parts) + "\n")
        except Exception:
            pass

    def _bind_geometry_tracking(self):
        """Keep the main window's last NORMAL geometry current as it moves and
        resizes, so closing it maximized (or minimized) still persists the
        un-maximized spot. <Configure> on the root also fires for every
        descendant widget, hence the identity check."""
        def _on_configure(event):
            if event.widget is self.root or str(event.widget) == ".":
                self._remember_geometry("main", self.root)
                self._maybe_reclaim_monitor()
        self.root.bind("<Configure>", _on_configure, add="+")

    # ── State Persistence ───────────────────────────────────────────────

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
                "excel": self.excel_enabled.get(),
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
        # Window geometries, keyed by monitor layout (other layouts' entries
        # are preserved; see _geometries_for_save for when this one is written)
        state["geometries"] = self._geometries_for_save(
            existing.get("geometries"), self._get_monitor_config_key())
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
        # Restore window geometries for the current monitor layout
        self._apply_geometry_entry(
            self._select_geometry_entry(state, self._get_monitor_config_key()))
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
        self.excel_enabled.set(entry.get("excel", False))
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
