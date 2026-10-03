"""Characterization tests for Other Setup (myagent/other_setup_mixin.py,
2026-10-04): the window background colour — one colour for the main window
and every dialog, chosen in the Other Setup dialog behind the button right of
Model Setup at the main window's bottom left, kept in the instance's state
file the way the window positions are.

Pinned here, without a key or the network (the widget tests build real
widgets on a withdrawn or transparent root and skip where no display exists):

* the colour helpers: #rrggbb through Tk's own lookup, the platform default
  read from the root once and by name, what the windows wear now, the option
  database patterns covering every class but the root;
* `_theme_apply` on real widgets: the chrome recoloured — the window, a
  frame, a label, a checkbutton, face, active and highlight looks alike —
  while content (a text box, an entry) and a button's face keep their own,
  as does a widget coloured on purpose (the INSTRUCTIONS band, a checkbutton
  painted white); a dialog open at the time goes with it; a window built
  AFTER it is born with the colour (the option database, an explicit bg=
  still winning); a second colour moves what wore the first; Default puts
  the platform colour back by name; an unknown colour is the default; the
  toolbar group's recorded resting highlight follows, so the next press
  paints the new colour back, not the old — on the REAL group too;
* the field colour (`_theme_apply_field` on real widgets): the content
  classes recoloured while the chrome keeps its own, a checkbutton embedded
  in a text box following the field and skipped by the chrome walk, later
  fields born with it (an explicit bg= still winning), the ttk side — the
  Treeview style's two colours, every combobox switched to the custom style
  and back, a later combobox born with it, one with a style of its own left
  alone, a dropdown list recoloured whether built before or after — the
  zebra shade, Default by name, and the two colours independent of each
  other;
* the title bar (Windows 11, the DWM call recorded and never made): the
  COLORREF and the black / white title text by brightness, a window shown
  before the colour coloured at once and reset by Default, a window shown
  after it coloured as it maps (the root through the Tk class hook, a
  dialog through the Toplevel one — no frame window exists before the first
  map), the hooks installed once, every toplevel found, and nothing asked
  off Windows;
* the state file (test_state_skill_modes's host): the key written only for a
  chosen colour, read back at load, absent = the default, a host without
  the mixin loading as before, a headless run writing nothing;
* the dialog on real widgets: what it opens on, Choose… (the picker stubbed)
  previewing on the spot, a dismissed picker changing nothing, Default, Save
  writing the state file and keeping the colour, Cancel and [X] putting the
  opening colour back, a second open lifting the first;
* the main window: Other Setup right of Model Setup in the same frame, the
  Tab order (output pane → Voice Setup → Model Setup → Other Setup → Debug),
  the press opening the dialog;
* the wiring (a static scan): the App's bases and `theme_bg` start, the state
  file's one writer and one reader, the main window's mnemonic, the dialog
  wired like the other setup dialogs with every geometry write through
  `_set_geometry`.
"""

import gc
import json
import os
import pathlib
import tempfile
import tkinter as tk
import unittest
from tkinter import ttk
from unittest import mock

from tests._util import stub
from tests.test_agent_request_display import _walk
from tests.test_state_skill_modes import _Host as _StateHost
from myagent import other_setup_mixin
from myagent.constants import (
    INSTR_TREE_STYLE, IS_WINDOWS, LIST_BAND_BG, LIST_TITLE_BG, LIST_ZEBRA_BG, TOOLBAR_ACTIVE_BG,
)
from myagent.other_setup_mixin import (
    DWM_COLOR_DEFAULT, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR, FIELD_COMBOBOX_STYLE,
    OtherSetupMixin, THEME_DB_PATTERNS, THEME_OPTIONS,
)
from myagent.ui_mixin import UIMixin, list_title_band

REPO = pathlib.Path(__file__).resolve().parents[1]
BLUE = "#c8e6f5"
PEACH = "#ffe0b2"


def tk_root(test, withdrawn=True):
    """A real root — withdrawn, or mapped but transparent (a transient of a
    withdrawn window never maps) — or a skip where no display exists."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:  # headless box, no display
        test.skipTest(f"Tk unavailable: {exc}")
    if withdrawn:
        root.withdraw()
    else:
        root.geometry("240x240+0+0")
        root.attributes("-alpha", 0.0)
    return root


class _ThemeHost(OtherSetupMixin):
    """The stand-ins are METHODS, not lambdas closing over the instance (the
    test_model_upgrade rule): a closure over its own host is a reference
    cycle, and a host holding Tk objects must never be finalised off the
    main thread."""

    def _place_window(self, win, kind, size, parent=None):
        return "+0+0"

    def _parse_geometry(self, geo):
        return None

    def _set_geometry(self, win, geo):
        win.geometry(geo)

    def _save_last_state(self):
        self.saves.append((self.theme_bg, getattr(self, "theme_field", None)))


class _GroupHost(OtherSetupMixin, UIMixin):
    """The real toolbar group over the real recolour."""


def host(root, cls=_ThemeHost, **attrs):
    base = dict(root=root, theme_bg=None, theme_field=None, _other_setup_dialog=None, saves=[])
    base.update(attrs)
    return stub(cls, **base)


def free_tk_garbage_then_destroy(test):
    """Drop the host, collect its cycles HERE on the main thread with the
    interpreter alive, and only then destroy the root."""
    test.h = None
    gc.collect()
    test.root.destroy()


# ── The colour helpers ──────────────────────────────────────────────────

class HelperTests(unittest.TestCase):

    def setUp(self):
        self.root = tk_root(self)
        self.h = host(self.root)

    def tearDown(self):
        free_tk_garbage_then_destroy(self)

    def test_hex_goes_through_tks_own_lookup(self):
        hx = OtherSetupMixin._theme_hex
        self.assertEqual(hx(self.root, "#abc"), "#aabbcc")
        self.assertEqual(hx(self.root, PEACH), PEACH)
        self.assertEqual(hx(self.root, "white"), "#ffffff")
        default = self.root.cget("background")               # SystemButtonFace on Windows
        r, g, b = (v >> 8 for v in self.root.winfo_rgb(default))
        self.assertEqual(hx(self.root, default), "#%02x%02x%02x" % (r, g, b))
        for bad in ("", "   ", None, 7, "not-a-colour", "#12"):
            self.assertIsNone(hx(self.root, bad), bad)

    def test_the_default_is_read_once_by_name_and_current_is_its_hex(self):
        name = self.root.cget("background")
        self.assertEqual(self.h._theme_default_bg(), name)
        self.assertEqual(self.h._theme_current(), OtherSetupMixin._theme_hex(self.root, name))
        self.root.configure(background=PEACH)       # recoloured behind the helper's back
        self.assertEqual(self.h._theme_default_bg(), name)       # still the one read first
        self.h.theme_bg = BLUE
        self.assertEqual(self.h._theme_current(), BLUE)

    def test_the_option_database_patterns_cover_every_class_but_the_root(self):
        classes = {p.split(".")[0].lstrip("*") for p in THEME_DB_PATTERNS}
        self.assertEqual(classes, set(THEME_OPTIONS) - {"Tk"})
        self.assertIn("*Frame.background", THEME_DB_PATTERNS)
        self.assertIn("*Checkbutton.activeBackground", THEME_DB_PATTERNS)
        self.assertIn("*Button.highlightBackground", THEME_DB_PATTERNS)
        self.assertNotIn("*Button.background", THEME_DB_PATTERNS)      # a button keeps its face
        for cls in ("Text", "Entry", "Listbox", "Spinbox", "Scrollbar", "Menu", "Button"):
            self.assertNotIn("background", THEME_OPTIONS.get(cls, ()), cls)


# ── _theme_apply on real widgets ───────────────────────────────────────

class ApplyTests(unittest.TestCase):

    def setUp(self):
        self.root = tk_root(self)
        self.h = host(self.root)

    def tearDown(self):
        free_tk_garbage_then_destroy(self)

    def rgb(self, color):
        return self.root.winfo_rgb(color)

    def wears(self, widget, opt="background"):
        return self.rgb(widget.cget(opt))

    def build(self, parent):
        """A slice of a MyAgent window: chrome, content, a button, and two
        widgets coloured on purpose (the band, a Safety-dialog checkbutton
        inside its white list)."""
        frame = tk.Frame(parent)
        w = {
            "frame": frame,
            "label": tk.Label(frame, text="Save Chat as"),
            "check": tk.Checkbutton(frame, text="Debug"),
            "button": tk.Button(frame, text="START"),
            "text": tk.Text(frame),
            "entry": tk.Entry(frame),
            "band": list_title_band(frame, "instructions"),
        }
        w["white"] = tk.Checkbutton(w["text"], text="pattern", bg="white", activebackground="white")
        return w

    def test_the_chrome_takes_the_colour_and_content_keeps_its_own(self):
        w = self.build(self.root)
        before = {k: self.wears(v) for k, v in w.items()}
        self.h._theme_apply(BLUE)
        blue = self.rgb(BLUE)
        self.assertEqual(self.h.theme_bg, BLUE)
        self.assertEqual(self.wears(self.root), blue)
        for name in ("frame", "label", "check"):
            self.assertEqual(self.wears(w[name]), blue, name)
        for name in ("label", "check"):
            self.assertEqual(self.wears(w[name], "activebackground"), blue, name)
        for name in ("frame", "label", "check", "button"):
            self.assertEqual(self.wears(w[name], "highlightbackground"), blue, name)
        # Content and a button's face keep their own.
        for name in ("button", "text", "entry"):
            self.assertEqual(self.wears(w[name]), before[name], name)
        # Coloured on purpose: untouched.
        self.assertEqual(self.wears(w["band"]), self.rgb(LIST_TITLE_BG))
        self.assertEqual(self.wears(w["white"]), self.rgb("white"))
        self.assertEqual(self.wears(w["white"], "activebackground"), self.rgb("white"))

    def test_a_dialog_open_at_the_time_goes_with_it(self):
        dlg = tk.Toplevel(self.root)
        dlg.withdraw()
        w = self.build(dlg)
        self.h._theme_apply(PEACH)
        peach = self.rgb(PEACH)
        self.assertEqual(self.wears(dlg), peach)
        self.assertEqual(self.wears(w["label"]), peach)
        self.assertEqual(self.wears(w["button"], "highlightbackground"), peach)
        self.assertEqual(self.wears(w["band"]), self.rgb(LIST_TITLE_BG))

    def test_a_window_built_afterwards_is_born_with_the_colour(self):
        plain_text = tk.Text(self.root).cget("background")
        self.h._theme_apply(BLUE)
        dlg = tk.Toplevel(self.root)
        dlg.withdraw()
        w = self.build(dlg)
        blue = self.rgb(BLUE)
        self.assertEqual(self.wears(dlg), blue)
        for name in ("frame", "label", "check"):
            self.assertEqual(self.wears(w[name]), blue, name)
        self.assertEqual(self.wears(w["check"], "activebackground"), blue)
        self.assertEqual(self.wears(w["button"], "highlightbackground"), blue)
        self.assertNotEqual(self.wears(w["button"]), blue)        # the face is a button's
        self.assertEqual(w["text"].cget("background"), plain_text)
        self.assertEqual(self.wears(w["white"]), self.rgb("white"))   # an explicit bg= wins
        self.assertEqual(self.wears(w["band"]), self.rgb(LIST_TITLE_BG))

    def test_a_second_colour_moves_what_wore_the_first(self):
        w = self.build(self.root)
        self.h._theme_apply(BLUE)
        self.h._theme_apply(PEACH)
        peach = self.rgb(PEACH)
        self.assertEqual(self.h.theme_bg, PEACH)
        for name in ("frame", "label", "check"):
            self.assertEqual(self.wears(w[name]), peach, name)
        self.assertEqual(self.wears(w["button"], "highlightbackground"), peach)
        self.assertEqual(self.wears(w["band"]), self.rgb(LIST_TITLE_BG))
        self.assertEqual(self.wears(tk.Label(self.root)), peach)        # born with the second

    def test_default_puts_the_platform_colour_back_by_name(self):
        name = self.root.cget("background")
        w = self.build(self.root)
        self.h._theme_apply(BLUE)
        self.h._theme_apply(None)
        self.assertIsNone(self.h.theme_bg)
        self.assertEqual(self.root.cget("background"), name)       # by NAME, not a hex copy
        self.assertEqual(w["label"].cget("background"), name)
        self.assertEqual(w["check"].cget("activebackground"), name)
        self.assertEqual(w["button"].cget("highlightbackground"), name)
        self.assertEqual(tk.Frame(self.root).cget("background"), name)   # born default again
        self.assertEqual(self.wears(w["band"]), self.rgb(LIST_TITLE_BG))

    def test_an_unknown_or_empty_colour_is_the_default(self):
        name = self.root.cget("background")
        w = self.build(self.root)
        for bad in ("not-a-colour", "", None, 12):
            self.h._theme_apply(BLUE)
            self.h._theme_apply(bad)
            self.assertIsNone(self.h.theme_bg, bad)
            self.assertEqual(w["frame"].cget("background"), name, bad)

    def test_the_toolbar_groups_recorded_resting_highlight_follows(self):
        name = self.root.cget("background")
        start, stop = tk.Button(self.root, text="START"), tk.Button(self.root, text="STOP")
        self.h._toolbar_styles = {
            start: {"rest": {"bg": name, "activebackground": name,
                             "highlightbackground": name},
                    "active": {"bg": TOOLBAR_ACTIVE_BG, "highlightbackground": TOOLBAR_ACTIVE_BG}},
            stop: {"rest": {"bg": name, "activebackground": name,
                            "highlightbackground": "#123456"},
                   "active": {"bg": TOOLBAR_ACTIVE_BG, "highlightbackground": TOOLBAR_ACTIVE_BG}},
        }
        self.h._theme_apply(BLUE)
        rest = self.h._toolbar_styles[start]["rest"]
        self.assertEqual(rest["highlightbackground"], BLUE)
        self.assertEqual(rest["bg"], name)                       # the face stays a button's
        self.assertEqual(self.h._toolbar_styles[stop]["rest"]["highlightbackground"], "#123456")
        self.assertEqual(self.h._toolbar_styles[start]["active"]["highlightbackground"],
                         TOOLBAR_ACTIVE_BG)
        self.h._theme_apply(None)                 # back to the default: it follows, by name
        self.assertEqual(rest["highlightbackground"], name)

    def test_a_pressed_toolbar_button_keeps_its_highlight(self):
        pressed = tk.Button(self.root, text="START", bg=TOOLBAR_ACTIVE_BG,
                            highlightbackground=TOOLBAR_ACTIVE_BG)
        self.h._theme_apply(BLUE)
        self.assertEqual(self.wears(pressed, "highlightbackground"), self.rgb(TOOLBAR_ACTIVE_BG))

    def test_pressing_the_real_group_after_a_recolour_paints_the_new_halo_back(self):
        self.h = host(self.root, cls=_GroupHost)
        start, stop = tk.Button(self.root, text="START"), tk.Button(self.root, text="STOP")
        self.h._track_toolbar_presses((start, lambda: None), (stop, lambda: None))
        self.h._theme_apply(BLUE)
        start.invoke()                  # START pressed: light blue
        stop.invoke()                   # ... and released by STOP's press
        self.assertEqual(self.wears(start, "highlightbackground"), self.rgb(BLUE))
        self.assertEqual(self.wears(stop, "highlightbackground"), self.rgb(TOOLBAR_ACTIVE_BG))
        self.assertNotEqual(self.wears(start), self.rgb(BLUE))        # the face: a button's


# ── The field colour ───────────────────────────────────────────────────

class FieldTests(unittest.TestCase):
    """_theme_apply_field on real widgets (a withdrawn root): the content
    classes, a widget embedded in a text box, the ttk styles, the zebra."""

    def setUp(self):
        self.root = tk_root(self)
        self.h = host(self.root)
        self.style = ttk.Style(self.root)

    def tearDown(self):
        free_tk_garbage_then_destroy(self)

    def rgb(self, color):
        return self.root.winfo_rgb(color)

    def wears(self, widget, opt="background"):
        return self.rgb(widget.cget(opt))

    def build(self, parent):
        """A slice of a MyAgent window: chrome, every field class, a
        combobox, an Instructions-style list with zebra rows, and a
        checkbutton embedded in a text box."""
        frame = tk.Frame(parent)
        w = {
            "frame": frame,
            "label": tk.Label(frame, text="Save Chat as"),
            "check": tk.Checkbutton(frame, text="Debug"),
            "button": tk.Button(frame, text="START"),
            "text": tk.Text(frame),
            "entry": tk.Entry(frame),
            "listbox": tk.Listbox(frame),
            "spinbox": tk.Spinbox(frame, from_=0, to=1),
            "combo": ttk.Combobox(frame, state="readonly", values=["a", "b"]),
            "tree": ttk.Treeview(frame, show="tree", style=INSTR_TREE_STYLE),
            "other_tree": ttk.Treeview(frame, show="tree"),
        }
        w["white"] = tk.Checkbutton(w["text"], text="pattern", bg="white", activebackground="white")
        for tree in (w["tree"], w["other_tree"]):      # the Instructions list's tags, as it configures them
            tree.tag_configure("section", background=LIST_BAND_BG)
            tree.tag_configure("unfiled", background=LIST_BAND_BG)
            tree.tag_configure("odd", background=LIST_ZEBRA_BG)
            section = tree.insert("", "end", text="SECTION", tags=("section",))
            tree.insert(section, "end", text="page", tags=("odd",))
        return w

    def tags(self, tree):
        return {tag: self.rgb(tree.tag_configure(tag, "background"))
                for tag in ("section", "unfiled", "odd")}

    def popdown_bg(self, combo):
        """The colour of the combobox's dropdown list — built by Tk on the
        Tcl side at the first drop (here: now), invisible to tkinter's
        winfo_children, so read by name."""
        combo.tk.call("ttk::combobox::PopdownWindow", combo)
        return self.rgb(combo.tk.call(f"{combo}.popdown.f.l", "cget", "-background"))

    def test_the_fields_take_the_colour_and_the_chrome_keeps_its_own(self):
        w = self.build(self.root)
        chrome_before = self.wears(self.root)
        before = {k: self.wears(v) for k, v in w.items()
                  if k not in ("combo", "tree", "other_tree")}
        self.h._theme_apply_field(PEACH)
        peach = self.rgb(PEACH)
        self.assertEqual(self.h.theme_field, PEACH)
        for name in ("text", "entry", "listbox", "spinbox"):
            self.assertEqual(self.wears(w[name]), peach, name)
        for name in ("frame", "label", "check", "button"):
            self.assertEqual(self.wears(w[name]), before[name], name)
        self.assertEqual(self.wears(self.root), chrome_before)
        self.assertIsNone(self.h.theme_bg)

    def test_a_widget_embedded_in_a_text_box_follows_the_field(self):
        w = self.build(self.root)
        self.h._theme_apply_field(PEACH)
        peach = self.rgb(PEACH)
        self.assertEqual(self.wears(w["white"]), peach)
        self.assertEqual(self.wears(w["white"], "activebackground"), peach)
        self.assertNotEqual(self.wears(w["check"]), peach)          # on a frame: chrome

    def test_the_chrome_walk_skips_an_embedded_widget(self):
        w = self.build(self.root)
        self.h._theme_apply("#ffffff")           # a white window: the embedded box wears it too
        self.h._theme_apply(BLUE)
        self.assertEqual(self.wears(w["check"]), self.rgb(BLUE))
        self.assertEqual(self.wears(w["white"]), self.rgb("white"))  # the text box's, not the window's

    def test_later_fields_are_born_with_it_and_an_explicit_colour_wins(self):
        self.h._theme_apply_field(PEACH)
        dlg = tk.Toplevel(self.root)
        dlg.withdraw()
        w = self.build(dlg)
        peach = self.rgb(PEACH)
        for name in ("text", "entry", "listbox", "spinbox"):
            self.assertEqual(self.wears(w[name]), peach, name)
        self.assertEqual(self.wears(tk.Entry(dlg, bg="white")), self.rgb("white"))
        self.assertEqual(self.wears(w["white"]), self.rgb("white"))   # explicit on purpose
        self.assertNotEqual(self.wears(w["label"]), peach)
        self.assertEqual(self.popdown_bg(w["combo"]), peach)         # a list built after: born with it

    def test_the_ttk_side_follows_and_default_puts_everything_back(self):
        w = self.build(self.root)
        own = ttk.Combobox(self.root, style="Own.TCombobox")
        tree_before = {opt: self.style.lookup("Treeview", opt)
                       for opt in ("background", "fieldbackground")}
        self.popdown_bg(w["combo"])                 # the dropdown list exists before the colour
        default_field = self.h._theme_field_default()
        self.h._theme_apply_field(PEACH)
        self.assertEqual(self.style.lookup("Treeview", "background"), PEACH)
        self.assertEqual(self.style.lookup("Treeview", "fieldbackground"), PEACH)
        self.assertEqual(self.popdown_bg(w["combo"]), self.rgb(PEACH))   # built before: walked
        expected = FIELD_COMBOBOX_STYLE if self.h._theme_combo_built else ""
        self.assertEqual(str(w["combo"].cget("style")), expected)
        self.assertEqual(str(ttk.Combobox(self.root).cget("style")), expected)   # born with it
        self.assertEqual(str(own.cget("style")), "Own.TCombobox")                # its own: untouched
        if self.h._theme_combo_built:
            self.assertEqual(self.style.lookup(FIELD_COMBOBOX_STYLE, "fieldbackground"), PEACH)
        zebra, band = self.h._theme_zebra(), self.h._theme_band()
        self.assertNotEqual(zebra, LIST_ZEBRA_BG)
        self.assertNotEqual(band, LIST_BAND_BG)
        self.assertEqual(self.tags(w["tree"]),
                         {"section": self.rgb(band), "unfiled": self.rgb(band), "odd": self.rgb(zebra)})
        # Another list, not the Instructions list: its tags are its own.
        self.assertEqual(self.tags(w["other_tree"]),
                         {"section": self.rgb(LIST_BAND_BG), "unfiled": self.rgb(LIST_BAND_BG),
                          "odd": self.rgb(LIST_ZEBRA_BG)})
        # Default: by name, styles back to what the theme had, the zebra too.
        self.h._theme_apply_field(None)
        self.assertIsNone(self.h.theme_field)
        for name in ("text", "entry", "listbox", "spinbox"):
            self.assertEqual(w[name].cget("background"), default_field, name)
        self.assertEqual(tk.Text(self.root).cget("background"), default_field)
        for opt, value in tree_before.items():
            self.assertEqual(self.style.lookup("Treeview", opt), value, opt)
        self.assertEqual(str(w["combo"].cget("style")), "")
        self.assertEqual(str(ttk.Combobox(self.root).cget("style")), "")
        self.assertEqual(self.tags(w["tree"]),
                         {"section": self.rgb(LIST_BAND_BG), "unfiled": self.rgb(LIST_BAND_BG),
                          "odd": self.rgb(LIST_ZEBRA_BG)})
        self.assertEqual(self.popdown_bg(w["combo"]), self.rgb(default_field))

    @unittest.skipUnless(IS_WINDOWS, "the custom combobox style is built on the vista layout")
    def test_the_combobox_style_keeps_the_native_border_and_arrow(self):
        self.h._theme_apply_field(PEACH)
        layout = str(self.style.layout(FIELD_COMBOBOX_STYLE))
        self.assertIn("Combobox.border", layout)
        self.assertIn("Combobox.rightdownarrow", layout)
        self.assertIn("Field.Combobox.fill", layout)
        self.assertNotIn("Combobox.background", layout)
        self.assertEqual(str(self.style.lookup(FIELD_COMBOBOX_STYLE, "borderwidth")), "0")
        # A combobox is the same size in either style.
        plain, themed = ttk.Combobox(self.root, style="TCombobox"), ttk.Combobox(self.root)
        self.assertEqual((plain.winfo_reqwidth(), plain.winfo_reqheight()),
                         (themed.winfo_reqwidth(), themed.winfo_reqheight()))

    def test_the_two_colours_are_independent(self):
        w = self.build(self.root)
        self.h._theme_apply(BLUE)
        self.h._theme_apply_field(PEACH)
        self.assertEqual((self.wears(w["label"]), self.wears(w["text"])),
                         (self.rgb(BLUE), self.rgb(PEACH)))
        self.h._theme_apply(None)                   # the window back: the fields stay
        self.assertEqual(self.wears(w["text"]), self.rgb(PEACH))
        self.assertEqual(w["label"].cget("background"), self.h._theme_default_bg())
        self.h._theme_apply(BLUE)
        self.h._theme_apply_field(None)             # the fields back: the window stays
        self.assertEqual(self.wears(w["label"]), self.rgb(BLUE))
        self.assertEqual(w["text"].cget("background"), self.h._theme_field_default())
        self.assertEqual((self.h.theme_bg, self.h.theme_field), (BLUE, None))

    def test_shade_zebra_and_band(self):
        shade = OtherSetupMixin._theme_shade
        self.assertEqual(shade(PEACH, 0.5), "#807059")
        self.assertEqual(shade("#f0f0f0", 1.5), "#ffffff")           # clamped
        self.assertEqual((self.h._theme_zebra(), self.h._theme_band()), (LIST_ZEBRA_BG, LIST_BAND_BG))
        self.h.theme_field = PEACH                                   # light: darker, the band more so
        self.assertEqual((self.h._theme_zebra(), self.h._theme_band()),
                         (shade(PEACH, 0.94), shade(PEACH, 0.88)))
        self.h.theme_field = "#1a237e"                               # dark: lighter, the band more so
        self.assertEqual((self.h._theme_zebra(), self.h._theme_band()),
                         (shade("#1a237e", 1.18), shade("#1a237e", 1.36)))

    def test_an_unknown_colour_is_the_default(self):
        w = self.build(self.root)
        self.h._theme_apply_field(PEACH)
        self.h._theme_apply_field("not-a-colour")
        self.assertIsNone(self.h.theme_field)
        self.assertEqual(w["text"].cget("background"), self.h._theme_field_default())


# ── The title bar (Windows 11) ─────────────────────────────────────────

def record_dwm(test):
    """Record every DwmSetWindowAttribute call the mixin would make."""
    test.calls = []
    patcher = mock.patch.object(
        OtherSetupMixin, "_theme_dwm_set",
        staticmethod(lambda hwnd, attribute, value: test.calls.append((hwnd, attribute, value))))
    patcher.start()
    test.addCleanup(patcher.stop)


class CaptionTests(unittest.TestCase):
    """The DWM calls recorded, never made. The root starts withdrawn and
    transparent, so a test decides when the frame window that owns the
    title bar comes into being."""

    def setUp(self):
        self.root = tk_root(self)
        self.root.attributes("-alpha", 0.0)
        self.h = host(self.root)
        record_dwm(self)

    def tearDown(self):
        free_tk_garbage_then_destroy(self)

    def show(self, win):
        win.deiconify()
        win.wait_visibility()
        self.root.update()

    def test_colorref_and_the_title_text_by_brightness(self):
        ref = OtherSetupMixin._theme_colorref
        self.assertEqual(ref(PEACH), 0x00B2E0FF)
        self.assertEqual(ref("#000000"), 0)
        self.assertEqual(ref("#ffffff"), 0x00FFFFFF)
        text = OtherSetupMixin._theme_caption_text
        self.assertEqual(text(BLUE), "#000000")
        self.assertEqual(text("#1a237e"), "#ffffff")       # a dark blue: white text
        self.assertEqual(text("#808080"), "#000000")       # the midpoint reads black

    def test_every_toplevel_is_found(self):
        dlg = tk.Toplevel(self.root)
        dlg.withdraw()
        tk.Frame(dlg)
        nested = tk.Toplevel(dlg)
        nested.withdraw()
        tk.Label(self.root)
        self.assertEqual(self.h._theme_toplevels(), [self.root, dlg, nested])

    @unittest.skipUnless(IS_WINDOWS, "the title bar is coloured on Windows only")
    def test_a_window_shown_before_the_colour_takes_it_at_once_and_default_resets(self):
        self.show(self.root)
        hwnd = OtherSetupMixin._theme_hwnd(self.root)
        self.assertTrue(hwnd)
        self.h._theme_apply(BLUE)
        blue = OtherSetupMixin._theme_colorref(BLUE)
        self.assertIn((hwnd, DWMWA_CAPTION_COLOR, blue), self.calls)
        self.assertIn((hwnd, DWMWA_TEXT_COLOR, 0x000000), self.calls)
        self.calls.clear()
        self.h._theme_apply("#1a237e")
        self.assertIn((hwnd, DWMWA_TEXT_COLOR, 0x00FFFFFF), self.calls)     # white on dark
        self.calls.clear()
        self.h._theme_apply(None)
        self.assertIn((hwnd, DWMWA_CAPTION_COLOR, DWM_COLOR_DEFAULT), self.calls)
        self.assertIn((hwnd, DWMWA_TEXT_COLOR, DWM_COLOR_DEFAULT), self.calls)

    @unittest.skipUnless(IS_WINDOWS, "the title bar is coloured on Windows only")
    def test_a_window_shown_after_the_colour_is_coloured_as_it_maps(self):
        self.assertEqual(OtherSetupMixin._theme_hwnd(self.root), 0)    # no frame before the first map
        self.h._theme_apply(BLUE)
        self.show(self.root)                                             # the root: the Tk class hook
        root_hwnd = OtherSetupMixin._theme_hwnd(self.root)
        self.assertTrue(root_hwnd)
        blue = OtherSetupMixin._theme_colorref(BLUE)
        self.assertIn((root_hwnd, DWMWA_CAPTION_COLOR, blue), self.calls)
        dlg = tk.Toplevel(self.root)                                      # a dialog, built withdrawn
        dlg.withdraw()
        dlg.attributes("-alpha", 0.0)
        self.calls.clear()
        self.show(dlg)                                                   # the Toplevel class hook
        dlg_hwnd = OtherSetupMixin._theme_hwnd(dlg)
        self.assertTrue(dlg_hwnd)
        self.assertIn((dlg_hwnd, DWMWA_CAPTION_COLOR, blue), self.calls)
        self.assertIn((dlg_hwnd, DWMWA_TEXT_COLOR, 0x000000), self.calls)

    @unittest.skipUnless(IS_WINDOWS, "the title bar is coloured on Windows only")
    def test_the_hooks_are_installed_once(self):
        self.h._theme_apply(BLUE)
        self.h._theme_apply(PEACH)
        for cls in ("Tk", "Toplevel"):
            self.assertEqual(self.root.bind_class(cls, "<Map>").count("_theme_on_map"), 1, cls)

    def test_off_windows_nothing_is_asked(self):
        with mock.patch.object(other_setup_mixin, "IS_WINDOWS", False):
            self.show(self.root)
            self.h._theme_apply(BLUE)
            self.h._theme_caption(self.root)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.root.bind_class("Toplevel", "<Map>"), "")
        self.assertEqual(self.root.cget("background"), BLUE)            # the chrome still took it


# ── The state file ──────────────────────────────────────────────────────

class _Recorder(_StateHost):
    """test_state_skill_modes's host with the mixin's one hook recorded."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.theme_bg = None
        self.theme_field = None
        self.applied = []
        self.applied_field = []

    def _theme_apply(self, color):
        self.applied.append(color)

    def _theme_apply_field(self, color):
        self.applied_field.append(color)


class StateFileTests(unittest.TestCase):

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.folder.name, "agent_state.json")

    def tearDown(self):
        self.folder.cleanup()

    def saved(self, h):
        h._save_last_state()
        with open(self.state_file, encoding="utf-8") as f:
            return json.load(f)

    def test_a_chosen_colour_is_written_and_read_back(self):
        h = _Recorder({}, self.state_file)
        h.theme_bg = PEACH
        self.assertEqual(self.saved(h)["background_color"], PEACH)
        again = _Recorder({}, self.state_file)
        again._load_last_state()
        self.assertEqual(again.applied, [PEACH])

    def test_the_default_writes_no_key_and_loads_as_the_default(self):
        h = _Recorder({}, self.state_file)
        self.assertNotIn("background_color", self.saved(h))
        again = _Recorder({}, self.state_file)
        again._load_last_state()
        self.assertEqual(again.applied, [None])

    def test_the_field_colour_has_its_own_key(self):
        h = _Recorder({}, self.state_file)
        h.theme_field = PEACH
        state = self.saved(h)
        self.assertEqual(state["field_color"], PEACH)
        self.assertNotIn("background_color", state)
        again = _Recorder({}, self.state_file)
        again._load_last_state()
        self.assertEqual((again.applied, again.applied_field), ([None], [PEACH]))
        h.theme_field = None
        self.assertNotIn("field_color", self.saved(h))

    def test_a_host_without_the_mixin_loads_as_before(self):
        h = _Recorder({}, self.state_file)
        h.theme_bg = PEACH
        self.saved(h)
        plain = _StateHost({}, self.state_file)
        plain._load_last_state()         # no _theme_apply: nothing called, nothing raised
        self.assertFalse(hasattr(plain, "theme_bg"))

    def test_a_headless_run_writes_nothing(self):
        h = _Recorder({}, self.state_file)
        h.theme_bg, h._headless = PEACH, True
        h._save_last_state()
        self.assertFalse(os.path.exists(self.state_file))


# ── The dialog (real widgets) ───────────────────────────────────────────

class DialogTests(unittest.TestCase):
    """The Other Setup dialog on real widgets — the test_model_upgrade recipe:
    a mapped, transparent root, the dialog driven once it is on screen, the
    platform's colour picker stubbed with the picks it should return."""

    def setUp(self):
        self.root = tk_root(self, withdrawn=False)

    def tearDown(self):
        free_tk_garbage_then_destroy(self)

    def open(self, act, picks=(), before=None):
        h = host(self.root)
        self.h = h
        if before is not None:
            before(h)
        picks = list(picks)
        seen = {}

        def when_up():
            # Once it is on screen: acting on the withdrawn window would
            # destroy it under its own wait_visibility.
            dlg = h._other_setup_dialog
            if dlg is not None and dlg.winfo_exists() and dlg.winfo_ismapped():
                seen["title"] = dlg.title()
                act(dlg, seen)
            else:
                self.root.after(20, when_up)

        self.root.after(20, when_up)
        watchdog = self.root.after(15000, lambda: h._other_setup_dialog.destroy())
        with mock.patch.object(other_setup_mixin.colorchooser, "askcolor",
                               side_effect=lambda **kw: picks.pop(0)) as picker:
            h._open_other_setup(self.root)
        self.root.after_cancel(watchdog)
        seen["picker_calls"] = picker.call_args_list
        return h, seen

    @staticmethod
    def widgets(dlg):
        """(buttons, label texts, the window row's swatch). The window row
        comes first, so its Choose… / Default keep the plain keys and the
        field row's are "Field Choose…" / "Field Default"."""
        buttons = {}
        for w in _walk(dlg):
            if isinstance(w, tk.Button):
                key = w.cget("text")
                if key in buttons:
                    key = "Field " + key
                buttons[key] = w
        labels = [w for w in _walk(dlg) if isinstance(w, tk.Label)]
        swatch = next(w for w in labels if str(w.cget("relief")) == "sunken")
        return buttons, [w.cget("text") for w in labels], swatch

    @staticmethod
    def field_swatch(dlg):
        return [w for w in _walk(dlg)
                if isinstance(w, tk.Label) and str(w.cget("relief")) == "sunken"][1]

    def rgb(self, color):
        return self.root.winfo_rgb(color)

    def test_it_opens_on_the_colour_the_windows_wear_and_cancel_puts_it_back(self):
        def act(dlg, seen):
            buttons, labels, swatch = self.widgets(dlg)
            seen["labels"] = labels
            seen["swatch_before"] = self.rgb(swatch.cget("background"))
            buttons["Choose…"].invoke()
            seen["previewed"] = (self.rgb(self.root.cget("background")),
                                 self.rgb(dlg.cget("background")),
                                 self.rgb(swatch.cget("background")))
            seen["after_labels"] = self.widgets(dlg)[1]
            buttons["Cancel"].invoke()

        h, seen = self.open(act, picks=[((255, 224, 178), PEACH)])
        default = h._theme_default_bg()
        self.assertEqual(seen["title"], "Other Setup")
        self.assertTrue(any("(the system default)" in t for t in seen["labels"]))
        self.assertEqual(seen["swatch_before"], self.rgb(default))
        # Choose… previewed the pick on every window at once, the swatch too...
        self.assertEqual(seen["previewed"], (self.rgb(PEACH),) * 3)
        self.assertIn(PEACH, seen["after_labels"])
        self.assertEqual(seen["picker_calls"][0].kwargs["initialcolor"],
                         OtherSetupMixin._theme_hex(self.root, default))
        # ... and Cancel put the opening colour back, saving nothing.
        self.assertIsNone(h.theme_bg)
        self.assertEqual(self.root.cget("background"), default)
        self.assertEqual(h.saves, [])
        self.assertIsNone(h._other_setup_dialog)

    def test_save_keeps_the_colour_and_writes_the_state_file(self):
        def act(dlg, seen):
            buttons, _labels, _swatch = self.widgets(dlg)
            buttons["Choose…"].invoke()
            buttons["Save"].invoke()

        h, _seen = self.open(act, picks=[((255, 224, 178), PEACH)])
        self.assertEqual(h.theme_bg, PEACH)
        self.assertEqual(h.saves, [(PEACH, None)])
        self.assertEqual(self.rgb(self.root.cget("background")), self.rgb(PEACH))
        self.assertEqual(self.rgb(tk.Label(self.root).cget("background")), self.rgb(PEACH))
        self.assertIsNone(h._other_setup_dialog)

    def test_a_dismissed_picker_changes_nothing(self):
        def act(dlg, seen):
            buttons, _labels, _swatch = self.widgets(dlg)
            buttons["Choose…"].invoke()
            seen["bg"] = self.root.cget("background")
            buttons["Cancel"].invoke()

        h, seen = self.open(act, picks=[(None, None)])
        self.assertEqual(seen["bg"], h._theme_default_bg())
        self.assertIsNone(h.theme_bg)
        self.assertEqual(h.saves, [])

    def test_default_puts_the_system_colour_back_and_save_writes_it(self):
        def act(dlg, seen):
            buttons, labels, swatch = self.widgets(dlg)
            seen["labels"] = labels
            seen["swatch"] = self.rgb(swatch.cget("background"))
            buttons["Default"].invoke()
            seen["bg"] = self.root.cget("background")
            seen["after_labels"] = self.widgets(dlg)[1]
            buttons["Save"].invoke()

        h, seen = self.open(act, before=lambda h: h._theme_apply(PEACH))
        self.assertIn(PEACH, seen["labels"])                   # opened on the chosen colour
        # One "(the system default)": the field row's; the window row shows its colour.
        self.assertEqual(sum("(the system default)" in t for t in seen["labels"]), 1)
        self.assertEqual(seen["swatch"], self.rgb(PEACH))
        self.assertEqual(seen["bg"], h._theme_default_bg())    # Default: back by name
        self.assertEqual(sum("(the system default)" in t for t in seen["after_labels"]), 2)
        self.assertIsNone(h.theme_bg)
        self.assertEqual(h.saves, [(None, None)])

    def test_x_is_cancel(self):
        def act(dlg, seen):
            buttons, _labels, _swatch = self.widgets(dlg)
            buttons["Choose…"].invoke()
            seen["previewed"] = self.rgb(self.root.cget("background"))
            dlg.tk.call(dlg.protocol("WM_DELETE_WINDOW"))      # the [X] handler

        h, seen = self.open(act, picks=[((200, 230, 245), BLUE)])
        self.assertEqual(seen["previewed"], self.rgb(BLUE))
        self.assertIsNone(h.theme_bg)
        self.assertEqual(self.root.cget("background"), h._theme_default_bg())
        self.assertEqual(h.saves, [])

    def test_a_second_open_lifts_the_first(self):
        def act(dlg, seen):
            self.h._open_other_setup(self.root)
            seen["same"] = self.h._other_setup_dialog is dlg
            seen["dialogs"] = sum(1 for w in self.root.winfo_children()
                                  if isinstance(w, tk.Toplevel))
            self.widgets(dlg)[0]["Cancel"].invoke()

        _h, seen = self.open(act)
        self.assertTrue(seen["same"])
        self.assertEqual(seen["dialogs"], 1)

    def test_the_field_row_is_independent_and_cancel_puts_both_back(self):
        def act(dlg, seen):
            buttons, labels, _swatch = self.widgets(dlg)
            seen["labels"] = labels
            buttons["Field Choose…"].invoke()
            seen["field"] = (self.root.cget("background"),             # the window: untouched
                             self.rgb(tk.Entry(self.root).cget("background")),   # a field: born peach
                             self.rgb(self.field_swatch(dlg).cget("background")))
            seen["after_labels"] = self.widgets(dlg)[1]
            buttons["Choose…"].invoke()                                 # the window row
            seen["both"] = (self.rgb(self.root.cget("background")),
                            self.rgb(tk.Entry(self.root).cget("background")))
            buttons["Cancel"].invoke()

        h, seen = self.open(act, picks=[((255, 224, 178), PEACH), ((200, 230, 245), BLUE)])
        self.assertEqual(sum("(the system default)" in t for t in seen["labels"]), 2)
        self.assertEqual(seen["field"], (h._theme_default_bg(), self.rgb(PEACH), self.rgb(PEACH)))
        self.assertEqual(sum("(the system default)" in t for t in seen["after_labels"]), 1)
        self.assertIn(PEACH, seen["after_labels"])
        self.assertEqual(seen["both"], (self.rgb(BLUE), self.rgb(PEACH)))
        self.assertEqual(seen["picker_calls"][0].kwargs["title"], "Field colour")
        self.assertEqual(seen["picker_calls"][1].kwargs["title"], "Window colour")
        # Cancel put both back.
        self.assertEqual((h.theme_bg, h.theme_field), (None, None))
        self.assertEqual(self.root.cget("background"), h._theme_default_bg())
        self.assertEqual(tk.Entry(self.root).cget("background"), h._theme_field_default())
        self.assertEqual(h.saves, [])

    def test_save_keeps_both_colours(self):
        def act(dlg, seen):
            buttons, _labels, _swatch = self.widgets(dlg)
            buttons["Field Choose…"].invoke()
            buttons["Choose…"].invoke()
            buttons["Save"].invoke()

        h, _seen = self.open(act, picks=[((255, 224, 178), PEACH), ((200, 230, 245), BLUE)])
        self.assertEqual((h.theme_bg, h.theme_field), (BLUE, PEACH))
        self.assertEqual(h.saves, [(BLUE, PEACH)])
        self.assertEqual(self.rgb(tk.Text(self.root).cget("background")), self.rgb(PEACH))

    def test_the_field_default_button_resets_only_the_fields(self):
        def act(dlg, seen):
            buttons, _labels, _swatch = self.widgets(dlg)
            buttons["Field Default"].invoke()
            seen["after"] = (self.root.cget("background"),
                             tk.Entry(self.root).cget("background"))
            buttons["Save"].invoke()

        h, seen = self.open(act, before=lambda h: (h._theme_apply(BLUE), h._theme_apply_field(PEACH)))
        self.assertEqual(seen["after"], (BLUE, h._theme_field_default()))
        self.assertEqual(h.saves, [(BLUE, None)])

    @unittest.skipUnless(IS_WINDOWS, "the title bar is coloured on Windows only")
    def test_the_dialogs_own_title_bar_follows_the_preview(self):
        record_dwm(self)

        def act(dlg, seen):
            seen["hwnd"] = OtherSetupMixin._theme_hwnd(dlg)
            buttons, _labels, _swatch = self.widgets(dlg)
            buttons["Choose…"].invoke()
            seen["after_pick"] = len(self.calls)
            buttons["Cancel"].invoke()

        _h, seen = self.open(act, picks=[((255, 224, 178), PEACH)])
        hwnd, root_hwnd = seen["hwnd"], OtherSetupMixin._theme_hwnd(self.root)
        self.assertTrue(hwnd)
        peach = OtherSetupMixin._theme_colorref(PEACH)
        picked, cancelled = self.calls[:seen["after_pick"]], self.calls[seen["after_pick"]:]
        # The pick previewed on the main window's title bar and the dialog's own...
        self.assertIn((root_hwnd, DWMWA_CAPTION_COLOR, peach), picked)
        self.assertIn((hwnd, DWMWA_CAPTION_COLOR, peach), picked)
        # ... and Cancel handed both their system title bar back.
        self.assertIn((root_hwnd, DWMWA_CAPTION_COLOR, DWM_COLOR_DEFAULT), cancelled)
        self.assertIn((hwnd, DWMWA_CAPTION_COLOR, DWM_COLOR_DEFAULT), cancelled)


# ── The main window ─────────────────────────────────────────────────────

class MainWindowTests(unittest.TestCase):
    """Other Setup on the REAL setup_ui (the test_voice recipe: a stub, a
    mapped but transparent root wide enough for the whole bottom row)."""

    def setUp(self):
        self.root = tk_root(self, withdrawn=False)
        self.opened = []
        app = stub(UIMixin, root=self.root, provider="OpenAI", model="m",
                   available_models=["m"], temperature=1.0)
        for name in ("debug_enabled", "tool_calls_enabled", "show_activity",
                     "show_thinking", "save_thinking", "diag_enabled"):
            setattr(app, name, tk.BooleanVar(master=self.root, value=False))
        app._update_title = lambda: None
        app._get_display_name = lambda model_id: model_id
        app.open_instruction_editor = app._start_agent = app._stop_agent = lambda: None
        app._voice_setup_from_main = lambda: self.opened.append("voice")
        app._upgrade_setup_from_main = lambda: self.opened.append("model")
        app._other_setup_from_main = lambda: self.opened.append("other")
        app.setup_ui()
        self.app = app
        self.root.geometry("1400x400+0+0")
        for _ in range(4):
            self.root.update()

    def tearDown(self):
        self.app = None
        gc.collect()
        self.root.destroy()

    def test_it_sits_right_of_model_setup_in_the_same_frame(self):
        voice, model, other = (self.app.voice_setup_button, self.app.model_setup_button,
                               self.app.other_setup_button)
        self.assertIs(other.master, self.app.setup_buttons)
        self.assertEqual(other.cget("text"), "Other Setup")
        self.assertGreaterEqual(model.winfo_x(), voice.winfo_x() + voice.winfo_width())
        self.assertGreaterEqual(other.winfo_x(), model.winfo_x() + model.winfo_width())
        self.assertEqual(other.winfo_y(), model.winfo_y())

    def test_tab_reaches_it_after_model_setup_and_before_the_checkboxes(self):
        self.assertIs(self.app.model_setup_button.tk_focusNext(), self.app.other_setup_button)
        self.assertIs(self.app.other_setup_button.tk_focusNext(), self.app.debug_toggle)

    def test_pressing_it_opens_other_setup(self):
        self.app.other_setup_button.invoke()
        self.assertEqual(self.opened, ["other"])


# ── Wiring ──────────────────────────────────────────────────────────────

class WiringTests(unittest.TestCase):

    def src(self, *parts):
        return REPO.joinpath(*parts).read_text(encoding="utf-8")

    def test_the_app_inherits_the_mixin_and_starts_on_the_default(self):
        src = self.src("MyAgent.py")
        self.assertIn("from myagent.other_setup_mixin import OtherSetupMixin", src)
        bases = src[src.index("class App("):src.index("def __init__")]
        self.assertIn("OtherSetupMixin", bases)
        init = src[src.index("def __init__"):]
        self.assertIn("self.theme_bg = None", init)
        self.assertIn("self.theme_field = None", init)
        self.assertLess(init.index("self.theme_bg = None"), init.index("self.setup_ui()"))

    def test_the_main_window_carries_the_button_right_of_model_setup(self):
        src = self.src("myagent", "ui_mixin.py")
        self.assertIn("command=self._other_setup_from_main", src)
        self.assertIn('"o": self.other_setup_button,', src)
        self.assertLess(src.index("self.model_setup_button = tk.Button("),
                        src.index("self.other_setup_button = tk.Button("))
        self.assertLess(src.index("self.other_setup_button = tk.Button("),
                        src.index("self.debug_toggle = tk.Checkbutton("))
        creation = src[src.index("self.other_setup_button = tk.Button("):][:200]
        self.assertIn('self.setup_buttons, text="Other Setup"', creation)   # the same frame

    def test_the_state_file_has_one_writer_and_one_reader(self):
        src = self.src("myagent", "state_mixin.py")
        save = src[src.index("def _save_last_state("):src.index("def _load_last_state(")]
        load = src[src.index("def _load_last_state("):src.index("def _periodic_save(")]
        for key, attr, apply in (("background_color", "theme_bg", "_theme_apply"),
                                 ("field_color", "theme_field", "_theme_apply_field")):
            self.assertEqual(src.count(f'"{key}"'), 2, key)
            self.assertIn(f'state["{key}"] = self.{attr}', save)
            self.assertIn(f'if getattr(self, "{attr}", None):', save)
            self.assertIn(f'self.{apply}(state.get("{key}"))', load)
            for name in sorted(os.listdir(REPO / "myagent")):      # nobody else touches the key
                if name.endswith(".py") and name != "state_mixin.py":
                    self.assertNotIn(f'"{key}"', self.src("myagent", name), name)
        self.assertIn('hasattr(self, "_theme_apply")', load)

    def test_the_instructions_list_takes_its_zebra_and_bands_from_the_theme(self):
        src = self.src("myagent", "instructions_mixin.py")
        self.assertIn('self._theme_zebra() if hasattr(self, "_theme_zebra") else LIST_ZEBRA_BG', src)
        self.assertIn('self._theme_band() if hasattr(self, "_theme_band") else LIST_BAND_BG', src)
        for literal in ('"#f3f3f3"', '"#e4e4e4"', '"Instr.Treeview"'):
            self.assertNotIn(literal, src, literal)
        self.assertEqual(src.count("INSTR_TREE_STYLE"), 3)         # the import, the style, the tree
        self.assertEqual((LIST_ZEBRA_BG, LIST_BAND_BG, INSTR_TREE_STYLE),
                         ("#f3f3f3", "#e4e4e4", "Instr.Treeview"))

    def test_the_dialog_is_wired_like_the_other_setup_dialogs(self):
        src = self.src("myagent", "other_setup_mixin.py")
        self.assertIn("bind_mnemonics(dlg, {", src)
        self.assertIn('"t": field_choose, "e": field_default,', src)
        self.assertIn('dlg.bind("<Escape>", cancel)', src)
        self.assertIn('dlg.protocol("WM_DELETE_WINDOW", cancel)', src)
        self.assertIn('self._place_window(dlg, "other_setup",', src)
        self.assertNotRegex(src, r"\.geometry\(\s*[^)\s]")     # every write through _set_geometry
        self.assertIn("self._set_geometry(dlg, ", src)
        self.assertIn("dlg.wait_visibility()", src)
        self.assertIn("dlg.grab_set()", src)
        self.assertNotIn("_save_instructions_to_disk", src)    # the colour is no instruction setting
        self.assertNotIn("upgrade_target", src)

    def test_the_title_bar_follows_the_colour_from_apply(self):
        src = self.src("myagent", "other_setup_mixin.py")
        body = src[src.index("    def _theme_apply("):src.index("    def _theme_recolor(")]
        self.assertIn("self._theme_install_hooks()", body)
        self.assertIn("self._theme_caption(win)", body)
        self.assertEqual((DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR, DWM_COLOR_DEFAULT),
                         (35, 36, 0xFFFFFFFF))
        self.assertIn('self.root.bind_class(cls, "<Map>", self._theme_on_map, add="+")', src)
        # Windows only: every DWM / user32 reach is behind the one flag.
        caption = src[src.index("    def _theme_caption("):src.index("    def _theme_toplevels(")]
        self.assertIn("if not IS_WINDOWS:", caption)
        hooks = src[src.index("    def _theme_install_hooks("):src.index("    # ── UI: Other Setup")]
        self.assertIn("if not IS_WINDOWS:", hooks)


if __name__ == "__main__":
    unittest.main()
