"""Other Setup — the window background colour (mixin #27, 2026-10-04).

Every MyAgent window wore Tk's platform default background — SystemButtonFace,
the light grey, on Windows; the system window colour on macOS. **Other
Setup**, the button right of Model Setup at the bottom left of the main
window (Alt+O), opens a small dialog for the settings that belong to no other
setup button — one so far, **Background colour** — and the colour chosen
there goes on the main window and every dialog at once: the Instruction
Editor, the Skills Manager, Safety, Confirm Command, Agent Request, Voice
Setup, Model Setup, the Section… prompt and Other Setup itself. It is kept
the way the window positions are: in this instance's state file
(`agent_state.json`, key `background_color`, written by `_save_last_state`
and read back by `_load_last_state`), per machine and per instance; a file
without the key means the platform default.

What "background" means. The chrome takes the colour — the windows
themselves, every Frame, Labelframe and PanedWindow, every Label,
Checkbutton and Radiobutton (face and active look alike) — while the things
that hold content keep theirs: text boxes, entries, lists and dropdowns stay
white, and a button keeps its face, because a button is a control, not
background. One part of a button IS background: the highlight frame around
it (1 px here, 3 on macOS — keyboard.install_focus_ring_defaults), painted
`highlightbackground` while it is unfocused, which at the platform default
would leave a grey halo around every button on a coloured window; so that
option follows the colour too, on buttons and on everything else that draws
one. A widget coloured on purpose — the INSTRUCTIONS / SKILLS bands
(LIST_TITLE_BG), the pressed toolbar button (TOOLBAR_ACTIVE_BG), Mike while
recording (VOICE_RECORDING_BG), the Safety dialog's checkbuttons inside
their white list — is never touched: `_theme_apply` recolours only the
options that still wear the colour the windows wore before, compared by RGB
(Tk names the default `SystemButtonFace`, a picked colour `#rrggbb`).

Two mechanisms, because Tk fills a widget's options once, at creation: the
windows open NOW are walked (`_theme_recolor`: every descendant of the root
— a dialog is the root's child in Tk's tree), and the windows to come take
the colour from the option database (THEME_DB_PATTERNS), which an explicit
`bg=` in the code still overrides. The toolbar's "last pressed" group
remembers each button's resting look from creation
(ui_mixin._track_toolbar_presses); a recolour refreshes the highlight frame
in those records too, or the next press would paint the old halo back
around the button it releases.

The title bar follows, on Windows 11: the Desktop Window Manager colours a
window's caption per window (`DwmSetWindowAttribute`, DWMWA_CAPTION_COLOR,
with DWMWA_TEXT_COLOR black or white by the colour's brightness), and
DWM_COLOR_DEFAULT hands the system title bar back. The frame window a title
bar belongs to exists only from a Tk toplevel's FIRST map (probed
2026-10-04), and every dialog is built withdrawn, so the windows shown now
are coloured by `_theme_apply` directly and every window shown later by a
`<Map>` class binding on Tk / Toplevel installed once
(`_theme_install_hooks`). Windows 10 answers an error, which is swallowed;
macOS and X11 are never asked.

The dialog previews: Choose… (the platform's colour picker) and Default each
recolour every open window on the spot; Save keeps the colour and writes
the state file at once; Cancel, Escape and [X] put back the colour the
dialog opened on. Pinned in tests/test_other_setup.py.
"""

import ctypes
import tkinter as tk
from tkinter import colorchooser

from myagent.constants import IS_WINDOWS
from myagent.keyboard import bind_mnemonics

# Widget class → the colour options that are "the window's background" on it.
# A button keeps its face (a control, not background); the highlight frame
# around it, painted highlightbackground while unfocused, is background.
THEME_OPTIONS = {
    "Tk": ("background", "highlightbackground"),
    "Toplevel": ("background", "highlightbackground"),
    "Frame": ("background", "highlightbackground"),
    "Labelframe": ("background", "highlightbackground"),
    "Panedwindow": ("background",),
    "Canvas": ("background", "highlightbackground"),
    "Label": ("background", "activebackground", "highlightbackground"),
    "Message": ("background", "highlightbackground"),
    "Checkbutton": ("background", "activebackground", "highlightbackground"),
    "Radiobutton": ("background", "activebackground", "highlightbackground"),
    "Scale": ("background", "activebackground", "highlightbackground"),
    "Button": ("highlightbackground",),
}
# The same options as the option database spells them, for every class but
# the root (it exists before any colour is chosen and is configured directly).
_DB_OPTION = {"background": "background", "activebackground": "activeBackground",
              "highlightbackground": "highlightBackground"}
THEME_DB_PATTERNS = tuple(
    f"*{cls}.{_DB_OPTION[opt]}"
    for cls, opts in THEME_OPTIONS.items() if cls != "Tk" for opt in opts)

# Windows 11 (build 22000+) colours a window's title bar per window through
# the Desktop Window Manager: DwmSetWindowAttribute with these attributes and
# a COLORREF (0x00BBGGRR); DWM_COLOR_DEFAULT puts the system colour back.
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWM_COLOR_DEFAULT = 0xFFFFFFFF

THEME_MUTED_FG = "#555555"
THEME_ERROR_FG = "#b00020"
THEME_NOTE = ("The main window and every dialog — the Instruction Editor, the Skills "
              "Manager, Safety, Agent Request, Confirm Command, Voice Setup, Model Setup "
              "and this one — take this colour at once; text boxes, lists and buttons "
              "keep their own. Kept with this instance's window positions in "
              "agent_state.json.")


class OtherSetupMixin:

    # ── The colour (no dialog) ─────────────────────────────────────────

    @staticmethod
    def _theme_hex(widget, color):
        """`color` as #rrggbb through Tk's own lookup — a name such as
        SystemButtonFace included — or None for one Tk does not know."""
        if not isinstance(color, str) or not color.strip():
            return None
        try:
            r, g, b = widget.winfo_rgb(color)
        except tk.TclError:
            return None
        return "#%02x%02x%02x" % (r >> 8, g >> 8, b >> 8)

    def _theme_default_bg(self):
        """The platform's own window background, by Tk's name for it
        (SystemButtonFace on Windows): what every window wears until a
        colour is chosen, read from the root once — before anything is
        recoloured — so Default can put it back by name."""
        name = getattr(self, "_theme_default", None)
        if name is None:
            name = self._theme_default = self.root.cget("background")
        return name

    def _theme_current(self):
        """#rrggbb of what the windows wear now."""
        return self._theme_hex(self.root, getattr(self, "theme_bg", None)
                               or self._theme_default_bg())

    def _theme_apply(self, color):
        """Give every window — open now, or opened later — the background
        `color` (#rrggbb or any Tk colour name; None, "" or a colour Tk does
        not know = the platform default) and keep it as `theme_bg`: the
        chosen colour as #rrggbb, or None for the default — the one field
        `_save_last_state` writes."""
        root = self.root
        before = root.winfo_rgb(self._theme_current())   # reads the default first
        chosen = self._theme_hex(root, color) if color else None
        self.theme_bg = chosen
        target = chosen or self._theme_default_bg()
        # Windows to come: Tk fills a widget's options at creation, from the
        # option database where the code names none (an explicit bg= wins).
        for pattern in THEME_DB_PATTERNS:
            root.option_add(pattern, target)
        # Windows open now — every dialog is a descendant of the root.
        self._theme_recolor(root, before, target)
        # The toolbar group's recorded resting looks (ui_mixin), or the next
        # press would paint the old highlight frame back around a button.
        for looks in (getattr(self, "_toolbar_styles", None) or {}).values():
            rest = looks.get("rest") or {}
            try:
                if root.winfo_rgb(rest["highlightbackground"]) == before:
                    rest["highlightbackground"] = target
            except (KeyError, tk.TclError):
                pass
        # The title bars (Windows 11): the windows shown now, and — through
        # the <Map> hooks — every window shown from here on, since the frame
        # a title bar belongs to exists only once its window has been shown.
        self._theme_install_hooks()
        for win in self._theme_toplevels():
            self._theme_caption(win)

    @classmethod
    def _theme_recolor(cls, widget, old_rgb, color):
        """Recolour `widget` and every descendant: each THEME_OPTIONS option
        still wearing `old_rgb` — the colour the windows wore before — is
        set to `color`; one wearing anything else was coloured on purpose
        and is left alone."""
        try:
            for opt in THEME_OPTIONS.get(widget.winfo_class(), ()):
                try:
                    if widget.winfo_rgb(widget.cget(opt)) == old_rgb:
                        widget.configure({opt: color})
                except tk.TclError:
                    pass            # an empty colour, or an option it lacks
            children = widget.winfo_children()
        except tk.TclError:
            return                  # a window mid-destruction
        for child in children:
            cls._theme_recolor(child, old_rgb, color)

    # ── The title bar (Windows 11) ─────────────────────────────────────

    @staticmethod
    def _theme_colorref(hex_color):
        """#rrggbb → the COLORREF (0x00BBGGRR) the Desktop Window Manager takes."""
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        return (b << 16) | (g << 8) | r

    @staticmethod
    def _theme_caption_text(hex_color):
        """The title text that reads on `hex_color`: black on a light colour,
        white on a dark one (perceived brightness, 128 the midpoint — in
        integers, so the midpoint itself is exact)."""
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        return "#000000" if 299 * r + 587 * g + 114 * b >= 128_000 else "#ffffff"

    @staticmethod
    def _theme_hwnd(win):
        """The frame window — the one wearing the title bar — of a Tk
        toplevel on Windows, or 0: Tk creates it at the window's FIRST map
        (probed 2026-10-04), so a window never shown has none yet."""
        try:
            user32 = ctypes.windll.user32
            user32.GetParent.argtypes = (ctypes.c_void_p,)
            user32.GetParent.restype = ctypes.c_void_p
            return int(user32.GetParent(win.winfo_id()) or 0)
        except (AttributeError, OSError, tk.TclError):
            return 0

    @staticmethod
    def _theme_dwm_set(hwnd, attribute, value):
        """One DwmSetWindowAttribute call with a DWORD value. A failure — a
        Windows without the attribute — is silent: that window keeps its
        system title bar."""
        try:
            dwm = ctypes.windll.dwmapi
            dwm.DwmSetWindowAttribute.argtypes = (ctypes.c_void_p, ctypes.c_uint,
                                                  ctypes.c_void_p, ctypes.c_uint)
            dwm.DwmSetWindowAttribute.restype = ctypes.c_long
            dwm.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(ctypes.c_uint(value)),
                                      ctypes.sizeof(ctypes.c_uint))
        except (AttributeError, OSError):
            pass

    def _theme_caption(self, win, retry=True):
        """Colour `win`'s title bar like its chrome (Windows 11; elsewhere a
        no-op): the chosen colour, with black or white text as its
        brightness asks, or the system title bar when no colour is chosen.
        A window with no frame yet is given one more try from the event
        loop, in case a <Map> ever lands ahead of the frame."""
        if not IS_WINDOWS:
            return
        hwnd = self._theme_hwnd(win)
        if not hwnd:
            if retry:
                try:
                    win.after(0, lambda: self._theme_caption(win, retry=False))
                except tk.TclError:
                    pass
            return
        chosen = getattr(self, "theme_bg", None)
        if chosen:
            caption = self._theme_colorref(chosen)
            text = self._theme_colorref(self._theme_caption_text(chosen))
        else:
            caption = text = DWM_COLOR_DEFAULT
        self._theme_dwm_set(hwnd, DWMWA_CAPTION_COLOR, caption)
        self._theme_dwm_set(hwnd, DWMWA_TEXT_COLOR, text)

    def _theme_toplevels(self):
        """The root and every Toplevel under it that still exists."""
        found = [self.root]

        def walk(widget):
            try:
                children = widget.winfo_children()
            except tk.TclError:
                return
            for child in children:
                try:
                    if child.winfo_class() == "Toplevel":
                        found.append(child)
                except tk.TclError:
                    continue
                walk(child)

        walk(self.root)
        return found

    def _theme_on_map(self, event):
        """A window just shown gets its title bar (the <Map> hooks below)."""
        if event.widget.winfo_class() in ("Tk", "Toplevel"):
            self._theme_caption(event.widget)

    def _theme_install_hooks(self):
        """Once per app: the root (class Tk) and every Toplevel get their
        title bar coloured as they are shown — every dialog is built
        withdrawn, and the frame that owns a title bar exists only from the
        first map, so no earlier walk can reach it. A toplevel's bindtags
        are (path, class, all), so the two class bindings see exactly the
        toplevels' own map events."""
        if getattr(self, "_theme_hooked", False):
            return
        self._theme_hooked = True
        if not IS_WINDOWS:
            return
        for cls in ("Tk", "Toplevel"):
            self.root.bind_class(cls, "<Map>", self._theme_on_map, add="+")

    # ── UI: Other Setup ────────────────────────────────────────────────

    def _other_setup_from_main(self):
        """The main window's Other Setup button (bottom left, Alt+O)."""
        self._open_other_setup(self.root)

    def _open_other_setup(self, parent):
        """The modal dialog over `parent` for the settings that belong to no
        other setup button — the window background colour, so far. Choose…
        and Default preview a colour on every open window at once; Save
        keeps it and writes the state file; Cancel (Escape, [X]) puts back
        the colour the dialog opened on."""
        existing = getattr(self, "_other_setup_dialog", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass
        opened_on = getattr(self, "theme_bg", None)

        dlg = tk.Toplevel(parent)
        self._other_setup_dialog = dlg
        dlg.withdraw()
        dlg.title("Other Setup")
        dlg.transient(parent)
        dlg.resizable(False, False)
        dlg.grid_columnconfigure(1, weight=1)
        font = ("Arial", 10)
        small = ("Arial", 9)

        tk.Label(dlg, text="Background colour:", font=font, anchor="w").grid(
            row=0, column=0, sticky="w", padx=(15, 8), pady=(14, 4))
        row = tk.Frame(dlg)
        row.grid(row=0, column=1, sticky="w", padx=(0, 15), pady=(14, 4))
        # The swatch wears the colour the windows wear. It is set outright at
        # each change — a Label, it would be caught by the preview's own walk
        # as well, but it should not depend on that.
        swatch = tk.Label(row, width=4, relief="sunken", bd=1)
        swatch.pack(side=tk.LEFT, padx=(0, 8), ipady=2)
        value = tk.Label(row, font=font, anchor="w", width=24)
        value.pack(side=tk.LEFT, padx=(0, 10))
        choose_btn = tk.Button(row, text="Choose…", width=10, command=lambda: choose())
        choose_btn.pack(side=tk.LEFT)
        default_btn = tk.Button(row, text="Default", width=10, command=lambda: use_default())
        default_btn.pack(side=tk.LEFT, padx=(6, 0))

        def wrapping(text, fg):
            # The Model Setup pattern: a modest starting wraplength keeps the
            # text from widening the dialog; once laid out, it wraps at the
            # width the row above gives the dialog.
            lbl = tk.Label(dlg, text=text, font=small, fg=fg, anchor="w", justify="left",
                           wraplength=440)
            lbl.bind("<Configure>", lambda e: lbl.config(wraplength=max(e.width - 4, 200)))
            return lbl

        note = wrapping(THEME_NOTE, THEME_MUTED_FG)
        note.grid(row=1, column=0, columnspan=2, sticky="ew", padx=15, pady=(6, 2))
        status = wrapping("", THEME_ERROR_FG)
        status.grid(row=2, column=0, columnspan=2, sticky="ew", padx=15)

        def show():
            current = self._theme_current()
            swatch.config(bg=current)
            value.config(text=current if getattr(self, "theme_bg", None)
                         else f"{current}  (the system default)")

        def choose():
            picked = colorchooser.askcolor(initialcolor=self._theme_current(), parent=dlg,
                                           title="Background colour")
            if picked and picked[1]:
                self._theme_apply(picked[1])
                show()

        def use_default():
            self._theme_apply(None)
            show()

        def close(event=None):
            self._other_setup_dialog = None
            dlg.destroy()
            return "break"

        def cancel(event=None):
            if getattr(self, "theme_bg", None) != opened_on:
                self._theme_apply(opened_on)
            return close()

        def save():
            try:
                self._save_last_state()
            except OSError as exc:
                status.config(text=f"Could not save the setting: {exc}")
                return
            close()

        btn_row = tk.Frame(dlg)
        btn_row.grid(row=3, column=0, columnspan=2, pady=(8, 12))
        save_btn = tk.Button(btn_row, text="Save", width=10, command=save)
        save_btn.pack(side=tk.LEFT, padx=8)
        cancel_btn = tk.Button(btn_row, text="Cancel", width=10, command=cancel)
        cancel_btn.pack(side=tk.LEFT, padx=8)
        show()

        dlg.protocol("WM_DELETE_WINDOW", cancel)
        dlg.bind("<Escape>", cancel)    # one small setting: no draft worth protecting
        bind_mnemonics(dlg, {"b": choose_btn, "d": default_btn,
                             "s": save_btn, "c": cancel_btn})

        dlg.update_idletasks()
        placed = self._place_window(dlg, "other_setup",
                                    (dlg.winfo_reqwidth(), dlg.winfo_reqheight()), parent=parent)
        # Keep the position, give the size back to Tk (the Model Setup
        # pattern): the note re-wraps to the dialog's width once laid out.
        position = self._parse_geometry(placed)
        self._set_geometry(dlg, "")
        if position:
            self._set_geometry(dlg, f"+{position[2]}+{position[3]}")
        dlg.deiconify()
        choose_btn.focus_set()
        dlg.wait_visibility()
        dlg.grab_set()
        dlg.wait_window()
