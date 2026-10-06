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

A second, independent colour (the user's request, later the same day) is the
**Field colour**: the background of everything that is white by default —
text boxes, entries, listboxes, the temperature spinbox, every combobox's
dropdown list and field, and the Instructions list (a ttk Treeview). Kept as
`field_color` beside `background_color` and applied by `_theme_apply_field`
with the same two mechanisms — the walk over what still wears the previous
field colour, the option database for later windows — plus ttk's styles,
which the option database does not reach: the Treeview style's background
and fieldbackground, and for comboboxes a style of our own
(FIELD_COMBOBOX_STYLE) whose layout is the native one with the inner fill
element — `Combobox.background`, drawn by the Windows theme engine, which
ignores every colour — replaced by Tk's default-theme `field` element, which
honours `fieldbackground`; the native border and arrow stay (probed
2026-10-04: identical at the default, the readonly focus highlight still
drawn). The style is built once; every combobox is switched to it while a
field colour is chosen and back to the default style for the system default,
later comboboxes through the option database's `*TCombobox.style`; where the
native layout has no such element (aqua) comboboxes keep their look — and on
aqua Tk 9 drops a native MENU down (`$cb.popdown.menu`, combobox.tcl's aqua
`PopdownWindow`), not a Listbox, so there is no dropdown list to colour there
either; the walk's `winfo exists` check leaves it alone. A
widget embedded in a text box (the Safety dialog's checkbuttons) wears the
text box's colour, so it belongs to the field walk and the chrome walk skips
it. The Instructions list's zebra stripes and section header bands become
shades of the field colour — the bands the deeper shade, so the three levels
keep their contrast (`_theme_zebra` / `_theme_band`; LIST_ZEBRA_BG /
LIST_BAND_BG at the default); the list is known by its style name,
INSTR_TREE_STYLE, so no other list is touched.

A third colour (the user's request, the same evening) is the **Button
colour**: a tk.Button's face (`background`) and its pressed / hovered face
(`activebackground`) — exactly the two options the window colour leaves
alone, so the three colours never touch one another's options even though
the window and button defaults share Tk's name, SystemButtonFace. Kept as
`button_color`, applied by `_theme_apply_button` with the same walk + option
database; the toolbar group's recorded resting faces follow as its highlight
frame does for the window colour. macOS Aqua draws a button's face natively
and ignores both options, so there buttons keep their native look. Text
stays black: no colour manages a foreground.

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
from tkinter import colorchooser, ttk

from myagent.constants import INSTR_TREE_STYLE, IS_WINDOWS, LIST_BAND_BG, LIST_ZEBRA_BG
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

# The field colour: the classes whose background is white by default — the
# content, not the chrome — and the option-database patterns for their
# later-born twins (a combobox's dropdown list is a plain Listbox, so the
# Listbox pattern reaches it too).
FIELD_OPTIONS = {
    "Text": ("background",),
    "Entry": ("background",),
    "Listbox": ("background",),
    "Spinbox": ("background",),
}
FIELD_DB_PATTERNS = tuple(f"*{cls}.background" for cls in FIELD_OPTIONS)
# A widget placed inside a text box (the Safety dialog's checkbuttons) wears
# the text box's colour: these classes follow the field colour there, and
# the chrome walk leaves them alone.
EMBEDDED_CLASSES = ("Checkbutton", "Radiobutton", "Label", "Frame")
# ttk draws its fields from styles, which the option database does not
# reach: the Treeview style's two colours, and for comboboxes a style of our
# own — the native layout with its inner fill element replaced by the
# default theme's `field`, which honours -fieldbackground where the Windows
# theme engine's element ignores every colour.
FIELD_COMBOBOX_STYLE = "Field.TCombobox"
FIELD_COMBOBOX_FILL = "Field.Combobox.fill"
FIELD_COMBOBOX_NATIVE_FILL = "Combobox.background"

# The button colour: a tk.Button's face and its pressed / hovered face — the
# two options the window colour leaves alone (it takes the highlight frame
# around the button, THEME_OPTIONS). macOS Aqua draws the face natively and
# ignores both, so there buttons keep their native look.
BUTTON_OPTIONS = ("background", "activebackground")
BUTTON_DB_PATTERNS = ("*Button.background", "*Button.activeBackground")

# Windows 11 (build 22000+) colours a window's title bar per window through
# the Desktop Window Manager: DwmSetWindowAttribute with these attributes and
# a COLORREF (0x00BBGGRR); DWM_COLOR_DEFAULT puts the system colour back.
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWM_COLOR_DEFAULT = 0xFFFFFFFF

THEME_MUTED_FG = "#555555"
THEME_ERROR_FG = "#b00020"
THEME_NOTE = ("Window colour: the main window and every dialog — the Instruction Editor, "
              "the Skills Manager, Safety, Agent Request, Confirm Command, Voice Setup, "
              "Model Setup and this one — and, on Windows 11, their title bars. Field "
              "colour: the text boxes, entries, lists, dropdowns and the Instructions "
              "list — everything that is white by default. Button colour: the face of "
              "every button (macOS keeps its native buttons). All three take effect at "
              "once and are kept with this instance's window positions in agent_state.json.")


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
        self._theme_patch_toolbar(before, target, ("highlightbackground",))
        # The title bars (Windows 11): the windows shown now, and — through
        # the <Map> hooks — every window shown from here on, since the frame
        # a title bar belongs to exists only once its window has been shown.
        self._theme_install_hooks()
        for win in self._theme_toplevels():
            self._theme_caption(win)

    def _theme_patch_toolbar(self, old_rgb, color, keys):
        """The toolbar group's recorded resting looks (ui_mixin's
        _track_toolbar_presses): each of `keys` still wearing `old_rgb`
        takes `color`, or the next press would paint the old look back on
        the button it releases."""
        for looks in (getattr(self, "_toolbar_styles", None) or {}).values():
            rest = looks.get("rest") or {}
            for key in keys:
                try:
                    if self.root.winfo_rgb(rest[key]) == old_rgb:
                        rest[key] = color
                except (KeyError, tk.TclError):
                    pass

    @classmethod
    def _theme_recolor(cls, widget, old_rgb, color):
        """Recolour `widget` and every descendant: each THEME_OPTIONS option
        still wearing `old_rgb` — the colour the windows wore before — is
        set to `color`; one wearing anything else was coloured on purpose
        and is left alone. A widget embedded in a text box is the field
        colour's (see _theme_recolor_fields) and is skipped here."""
        try:
            embedded = widget.master is not None and widget.master.winfo_class() == "Text"
            for opt in () if embedded else THEME_OPTIONS.get(widget.winfo_class(), ()):
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

    # ── The field colour ───────────────────────────────────────────────

    def _theme_field_default(self):
        """The platform's own field background by Tk's name for it
        (SystemWindow on Windows), read once from a throwaway entry BEFORE
        the option database is touched, so Default can put it back by name."""
        name = getattr(self, "_theme_field_default_name", None)
        if name is None:
            probe = tk.Entry(self.root)
            name = self._theme_field_default_name = probe.cget("background")
            probe.destroy()
        return name

    def _theme_field_current(self):
        """#rrggbb of what the fields wear now."""
        return self._theme_hex(self.root, getattr(self, "theme_field", None)
                               or self._theme_field_default())

    @staticmethod
    def _theme_shade(hex_color, factor):
        """`hex_color` with every channel scaled by `factor`, clamped."""
        r, g, b = (min(255, max(0, round(int(hex_color[i:i + 2], 16) * factor)))
                   for i in (1, 3, 5))
        return "#%02x%02x%02x" % (r, g, b)

    def _theme_zebra(self):
        """The Instructions list's alternate-row colour: LIST_ZEBRA_BG on
        the default field colour, else a shade of the chosen one — a little
        darker on a light colour, lighter on a dark one."""
        chosen = getattr(self, "theme_field", None)
        if not chosen:
            return LIST_ZEBRA_BG
        light = self._theme_caption_text(chosen) == "#000000"
        return self._theme_shade(chosen, 0.94 if light else 1.18)

    def _theme_band(self):
        """The Instructions list's section header bands: LIST_BAND_BG on the
        default field colour, else a deeper shade of the chosen one than the
        zebra rows — darker on a light colour, lighter on a dark one."""
        chosen = getattr(self, "theme_field", None)
        if not chosen:
            return LIST_BAND_BG
        light = self._theme_caption_text(chosen) == "#000000"
        return self._theme_shade(chosen, 0.88 if light else 1.36)

    def _theme_apply_field(self, color):
        """Give every field — open now, or opened later — the background
        `color` (#rrggbb or any Tk colour name; None, "" or a colour Tk does
        not know = the platform default) and keep it as `theme_field`
        (#rrggbb or None), the one field `_save_last_state` writes under
        `field_color`. Independent of the window colour."""
        root = self.root
        before = root.winfo_rgb(self._theme_field_current())   # reads the default first
        chosen = self._theme_hex(root, color) if color else None
        self.theme_field = chosen
        target = chosen or self._theme_field_default()
        for pattern in FIELD_DB_PATTERNS:
            root.option_add(pattern, target)
        combo_style = self._theme_style_fields(chosen)
        self._theme_recolor_fields(root, before, target, combo_style)

    def _theme_style_fields(self, chosen):
        """The ttk side: the Treeview style's colours and the combobox style
        (built at first need). Returns the style every combobox should
        wear — FIELD_COMBOBOX_STYLE while a colour is chosen and the style
        could be built, else "" (the default style) — and tells the option
        database, for the comboboxes to come."""
        style = ttk.Style(self.root)
        defaults = getattr(self, "_theme_tree_defaults", None)
        if defaults is None:
            defaults = self._theme_tree_defaults = {
                opt: style.lookup("Treeview", opt) for opt in ("background", "fieldbackground")}
        combo_style = ""
        if chosen:
            style.configure("Treeview", background=chosen, fieldbackground=chosen)
            if self._theme_combobox_style(style):
                style.configure(FIELD_COMBOBOX_STYLE, fieldbackground=chosen)
                style.map(FIELD_COMBOBOX_STYLE, fieldbackground=[("readonly", chosen)])
                combo_style = FIELD_COMBOBOX_STYLE
        else:
            style.configure("Treeview", **defaults)
        self.root.option_add("*TCombobox.style", combo_style)
        return combo_style

    def _theme_combobox_style(self, style):
        """Build FIELD_COMBOBOX_STYLE once: the native TCombobox layout with
        its inner fill element swapped for the default theme's `field`
        (zero border and focus width, so the size stays the native one).
        False where the layout has no such element — another theme, aqua —
        and comboboxes then keep their native look."""
        built = getattr(self, "_theme_combo_built", None)
        if built is not None:
            return built
        try:
            style.element_create(FIELD_COMBOBOX_FILL, "from", "default", "field")
        except tk.TclError:
            pass                                    # already there: one per interpreter

        def swap(layout):
            out, found = [], False
            for name, spec in layout:
                spec = dict(spec)
                if "children" in spec:
                    spec["children"], inner = swap(spec["children"])
                    found = found or inner
                if name == FIELD_COMBOBOX_NATIVE_FILL:
                    name, found = FIELD_COMBOBOX_FILL, True
                out.append((name, spec))
            return out, found

        try:
            layout, found = swap(style.layout("TCombobox"))
            if found:
                style.layout(FIELD_COMBOBOX_STYLE, layout)
                style.configure(FIELD_COMBOBOX_STYLE, borderwidth=0, focuswidth=0)
        except tk.TclError:
            found = False
        self._theme_combo_built = found
        return found

    def _theme_recolor_fields(self, widget, old_rgb, color, combo_style):
        """Recolour every field under `widget` (itself included) that still
        wears `old_rgb`: the FIELD_OPTIONS classes, a widget embedded in a
        text box, a combobox's dropdown list (a Listbox under it); switch
        every combobox wearing a default style to `combo_style`; and give
        the Instructions list — known by its style — its new zebra and band
        shades."""
        try:
            cls_name = widget.winfo_class()
            options = FIELD_OPTIONS.get(cls_name, ())
            if (not options and cls_name in EMBEDDED_CLASSES and widget.master is not None
                    and widget.master.winfo_class() == "Text"):
                options = ("background", "activebackground")
            for opt in options:
                try:
                    if widget.winfo_rgb(widget.cget(opt)) == old_rgb:
                        widget.configure({opt: color})
                except tk.TclError:
                    pass
            if cls_name == "TCombobox":
                if str(widget.cget("style")) in ("", "TCombobox", FIELD_COMBOBOX_STYLE):
                    widget.configure(style=combo_style)
                # Its dropdown list, built by Tk on the Tcl side at the first
                # drop: tkinter's winfo_children skips it, so it is reached
                # by name. None on aqua, where Tk 9 drops a native menu
                # (`.popdown.menu`) — the exists check is what skips it.
                popdown = f"{widget}.popdown.f.l"
                if int(widget.tk.call("winfo", "exists", popdown)):
                    current = widget.tk.call(popdown, "cget", "-background")
                    if widget.winfo_rgb(current) == old_rgb:
                        widget.tk.call(popdown, "configure", "-background", color)
            if cls_name == "Treeview" and str(widget.cget("style")) == INSTR_TREE_STYLE:
                band = self._theme_band()
                widget.tag_configure("odd", background=self._theme_zebra())
                widget.tag_configure("section", background=band)
                widget.tag_configure("unfiled", background=band)
            children = widget.winfo_children()
        except tk.TclError:
            return                  # a window mid-destruction
        for child in children:
            self._theme_recolor_fields(child, old_rgb, color, combo_style)

    # ── The button colour ──────────────────────────────────────────────

    def _theme_button_default(self):
        """The platform's own button face by Tk's name for it
        (SystemButtonFace on Windows), read once from a throwaway button
        BEFORE the option database is touched, so Default can put it back
        by name."""
        name = getattr(self, "_theme_button_default_name", None)
        if name is None:
            probe = tk.Button(self.root)
            name = self._theme_button_default_name = probe.cget("background")
            probe.destroy()
        return name

    def _theme_button_current(self):
        """#rrggbb of what the buttons wear now."""
        return self._theme_hex(self.root, getattr(self, "theme_button", None)
                               or self._theme_button_default())

    def _theme_apply_button(self, color):
        """Give every button — open now, or opened later — the face
        `color` (#rrggbb or any Tk colour name; None, "" or a colour Tk does
        not know = the platform default) and keep it as `theme_button`
        (#rrggbb or None), the one field `_save_last_state` writes under
        `button_color`. Independent of the other two colours."""
        root = self.root
        before = root.winfo_rgb(self._theme_button_current())   # reads the default first
        chosen = self._theme_hex(root, color) if color else None
        self.theme_button = chosen
        target = chosen or self._theme_button_default()
        for pattern in BUTTON_DB_PATTERNS:
            root.option_add(pattern, target)
        self._theme_recolor_buttons(root, before, target)
        # The toolbar group's recorded resting faces (ui_mixin), or the next
        # press would paint the old face back on the button it releases.
        self._theme_patch_toolbar(before, target, ("bg", "activebackground"))

    def _theme_recolor_buttons(self, widget, old_rgb, color):
        """Recolour every button under `widget` (itself included) whose
        face still wears `old_rgb`; a button coloured on purpose — the
        pressed toolbar button, a recording Mike — is left alone."""
        try:
            if widget.winfo_class() == "Button":
                for opt in BUTTON_OPTIONS:
                    try:
                        if widget.winfo_rgb(widget.cget(opt)) == old_rgb:
                            widget.configure({opt: color})
                    except tk.TclError:
                        pass
            children = widget.winfo_children()
        except tk.TclError:
            return                  # a window mid-destruction
        for child in children:
            self._theme_recolor_buttons(child, old_rgb, color)

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
        other setup button — the window, field and button colours, so far,
        a row each. Choose… and Default preview a colour on every open
        window at once; Save keeps them all and writes the state file;
        Cancel (Escape, [X]) puts back the colours the dialog opened on."""
        existing = getattr(self, "_other_setup_dialog", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass
        opened_on = (getattr(self, "theme_bg", None), getattr(self, "theme_field", None),
                     getattr(self, "theme_button", None))

        dlg = tk.Toplevel(parent)
        self._other_setup_dialog = dlg
        dlg.withdraw()
        dlg.title("Other Setup")
        dlg.transient(parent)
        dlg.resizable(False, False)
        dlg.grid_columnconfigure(1, weight=1)
        font = ("Arial", 10)
        small = ("Arial", 9)
        shows = []

        def refresh():
            for show in shows:
                show()

        def colour_row(index, label, current, chosen, apply):
            """One colour: a swatch, the hex (or the system default), Choose…
            and Default. The swatch is set outright at each change — a
            Label, it would be caught by the preview's own walk as well,
            but it should not depend on that."""
            pady = (14, 4) if index == 0 else 4
            tk.Label(dlg, text=label, font=font, anchor="w").grid(
                row=index, column=0, sticky="w", padx=(15, 8), pady=pady)
            frame = tk.Frame(dlg)
            frame.grid(row=index, column=1, sticky="w", padx=(0, 15), pady=pady)
            swatch = tk.Label(frame, width=4, relief="sunken", bd=1)
            swatch.pack(side=tk.LEFT, padx=(0, 8), ipady=2)
            value = tk.Label(frame, font=font, anchor="w", width=24)
            value.pack(side=tk.LEFT, padx=(0, 10))

            def show():
                now = current()
                swatch.config(bg=now)
                value.config(text=now if chosen() else f"{now}  (the system default)")

            def choose():
                picked = colorchooser.askcolor(initialcolor=current(), parent=dlg,
                                               title=label.rstrip(":"))
                if picked and picked[1]:
                    apply(picked[1])
                    refresh()

            def use_default():
                apply(None)
                refresh()

            choose_btn = tk.Button(frame, text="Choose…", width=10, command=choose)
            choose_btn.pack(side=tk.LEFT)
            default_btn = tk.Button(frame, text="Default", width=10, command=use_default)
            default_btn.pack(side=tk.LEFT, padx=(6, 0))
            shows.append(show)
            show()
            return choose_btn, default_btn

        bg_choose, bg_default = colour_row(
            0, "Window colour:", self._theme_current,
            lambda: getattr(self, "theme_bg", None), self._theme_apply)
        field_choose, field_default = colour_row(
            1, "Field colour:", self._theme_field_current,
            lambda: getattr(self, "theme_field", None), self._theme_apply_field)
        button_choose, button_default = colour_row(
            2, "Button colour:", self._theme_button_current,
            lambda: getattr(self, "theme_button", None), self._theme_apply_button)

        def wrapping(text, fg):
            # The Model Setup pattern: a modest starting wraplength keeps the
            # text from widening the dialog; once laid out, it wraps at the
            # width the rows above give the dialog.
            lbl = tk.Label(dlg, text=text, font=small, fg=fg, anchor="w", justify="left",
                           wraplength=440)
            lbl.bind("<Configure>", lambda e: lbl.config(wraplength=max(e.width - 4, 200)))
            return lbl

        note = wrapping(THEME_NOTE, THEME_MUTED_FG)
        note.grid(row=3, column=0, columnspan=2, sticky="ew", padx=15, pady=(6, 2))
        status = wrapping("", THEME_ERROR_FG)
        status.grid(row=4, column=0, columnspan=2, sticky="ew", padx=15)

        def close(event=None):
            self._other_setup_dialog = None
            dlg.destroy()
            return "break"

        def cancel(event=None):
            if getattr(self, "theme_bg", None) != opened_on[0]:
                self._theme_apply(opened_on[0])
            if getattr(self, "theme_field", None) != opened_on[1]:
                self._theme_apply_field(opened_on[1])
            if getattr(self, "theme_button", None) != opened_on[2]:
                self._theme_apply_button(opened_on[2])
            return close()

        def save():
            try:
                self._save_last_state()
            except OSError as exc:
                status.config(text=f"Could not save the settings: {exc}")
                return
            close()

        btn_row = tk.Frame(dlg)
        btn_row.grid(row=5, column=0, columnspan=2, pady=(8, 12))
        save_btn = tk.Button(btn_row, text="Save", width=10, command=save)
        save_btn.pack(side=tk.LEFT, padx=8)
        cancel_btn = tk.Button(btn_row, text="Cancel", width=10, command=cancel)
        cancel_btn.pack(side=tk.LEFT, padx=8)

        dlg.protocol("WM_DELETE_WINDOW", cancel)
        dlg.bind("<Escape>", cancel)    # three small settings: no draft worth protecting
        bind_mnemonics(dlg, {"b": bg_choose, "d": bg_default,
                             "t": field_choose, "e": field_default,
                             "u": button_choose, "r": button_default,
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
        bg_choose.focus_set()
        dlg.wait_visibility()
        dlg.grab_set()
        dlg.wait_window()
