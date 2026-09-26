"""Keyboard operation of MyAgent's windows (2026-09-10): the mouse is optional.

Every control is already a Tab stop (Tk's default traversal), so this module
adds only the three things Tk leaves out, plus two workarounds:

* **A safe way out of a text box.** Inside a Text widget Tab TYPES a tab, so
  Escape moves the keyboard focus to the next control instead. It never
  inserts, deletes, submits or closes anything. Ctrl+Tab / Ctrl+Shift+Tab (Tk's
  own Text bindings) do the same forwards / backwards. The same Escape leaves
  an Entry, a Spinbox, a Combobox and a Treeview (the Instruction Editor's
  Instructions list) too, so "Escape = leave this field" holds
  everywhere. Installed ONCE per Tk instance as class bindings
  (`install_class_bindings`), so every field in every window, including ones
  added later, behaves alike.
* **Return presses the focused button** (Tk only had Space), through the same
  `tk::ButtonInvoke` Space uses, so it flashes exactly like a click.
* **Alt+letter accelerators** (`bind_mnemonics`): Alt+<letter> anywhere in a
  window presses a button or focuses a field. No underline cue is drawn on
  the widgets (the user prefers the plain look); the letters are documented
  per window in the README's Keyboard operation section.
* `link_embedded_checkbuttons`: the Safety dialog keeps its checkbuttons inside
  a scrolled Text, and Tk's Tab traversal skips a widget scrolled out of view,
  so Tab / Shift+Tab / Down / Up step through them and scroll each into view.
* `install_focus_ring_defaults`: on macOS, a button focus ring Tk can actually
  draw. Tk 9.0.3 on macOS 26 draws the default 1-px ring of a focused
  tk.Button shifted by its frame's offset — a stray black line across a
  button near the window's top-left, no ring at all further down
  (2026-09-15) — so there every tk.Button gets a 3-px ring, the width from
  which Tk leaves the drawing to Aqua, which paints the system's rounded
  blue ring where it belongs. Windows draws its dotted ring correctly and is
  untouched. SelfBot carries a byte-identical in-file copy.
* `install_scrollbar_defaults`: a scrollbar is never a Tab stop. Tk's fallback
  rule for a widget whose `takefocus` is unset (`tk::FocusOK`) counts any
  viewable, enabled widget whose class carries a Key binding, and Tk 9.0.3's
  Scrollbar class binds Page Up / Page Down — so on the Mac Tab from the
  output pane landed on its scrollbar instead of Voice Setup, and the same
  rule put every scrollbar created right after a text box in Escape's path
  (2026-09-26). The option database gives every LATER tk.Scrollbar a
  `takefocus` of 0, on every platform. SelfBot carries a byte-identical
  in-file copy.

Everything here is plain widget configuration on Tk objects: no App state, so
the helpers are shared by every mixin that builds a window and are unit-tested
on a bare Tk root.
"""

# Tk's own press procedures: the same flash as Space, and a no-op when disabled.
_INVOKE = {
    "Button": "tk::ButtonInvoke",
    "Checkbutton": "tk::CheckRadioInvoke",
    "Radiobutton": "tk::CheckRadioInvoke",
}

_FIELD_CLASSES = ("Text", "Entry", "Spinbox", "TCombobox", "Treeview")


def install_focus_ring_defaults(root):
    """Once per Tk instance, BEFORE any button exists: a focus ring Tk can draw.

    A focused tk.Button wears a ring `highlightthickness` wide. Under 3 Tk
    draws that ring itself, and Tk 9.0.3 on macOS 26 (2026-09-15) draws it
    shifted up-left by the parent frame's offset within the window: on a
    button whose frame sits within a button's size of the window's top-left
    corner only the ring's bottom and right edges land inside the widget — a
    stray black line struck through the label with a tick at one corner (the
    artefact over MyAgent's Instruction at startup, which holds the initial
    focus, and over SelfBot's Skills Manager SAVE / DELETE / NEW) — and on
    any button further down the ring misses the widget entirely, so there is
    no focus cue at all (SelfBot's main-window buttons). From 3 up Tk leaves
    the ring to Aqua, which draws its own rounded blue one in the right place
    (probed live: 1 and 2 garbage or nothing; 3, 4 and 6 the system ring).
    So on Aqua the option database gives every LATER tk.Button a thickness
    of 3. An explicit `highlightthickness=` on a widget still wins (SelfBot's
    Auto toggle keeps its 0), a widget that already exists is not touched,
    and no other class is (ttk buttons have no such option; checkbuttons
    draw nothing wrong at their default). Windows and X11 draw their default
    ring correctly (the dotted rectangle) and are left alone. One deliberate
    side effect on the Mac: the ring area is also the frame the toolbar
    paints light blue for its "last pressed" button, so that frame is 3 px
    there, and each button grows 2 px per side.
    """
    if root.tk.call("tk", "windowingsystem") == "aqua":
        root.option_add("*Button.highlightThickness", 3)


def install_scrollbar_defaults(root):
    """Once per Tk instance, BEFORE any scrollbar exists: none is a Tab stop.

    A widget whose `takefocus` is unset is a Tab stop by Tk's fallback rule
    (`tk::FocusOK`): viewable, not disabled, and its class carries some Key
    binding. Tk 9.0.3's Scrollbar class binds Page Up / Page Down, so on the
    Mac (2026-09-26) Tab from the read-only output pane landed on its
    scrollbar instead of Voice Setup, and the same rule put every scrollbar
    created right after a text box — the editor's, the Skills Manager's, the
    dialogs' — in the path of Escape's `focus_next`. A scrollbar has nothing
    to offer the keyboard (the widget it scrolls takes the same keys), so the
    option database gives every LATER tk.Scrollbar a `takefocus` of 0. On
    every platform: the intended Tab order is the same everywhere, and where
    a scrollbar was never a stop this changes nothing. An explicit
    `takefocus=` on a widget still wins, a scrollbar that already exists is
    not touched, and ttk scrollbars (class TScrollbar) are not covered —
    MyAgent has none.
    """
    root.option_add("*Scrollbar.takeFocus", "0")


def install_class_bindings(root):
    """Once per Tk instance: Escape leaves any field, Return presses any button."""
    for cls in _FIELD_CLASSES:
        root.bind_class(cls, "<Escape>", leave_field)
    for seq in ("<Return>", "<KP_Enter>"):
        root.bind_class("Button", seq, press_button)


def leave_field(event):
    """Escape inside a field: focus the next control, change nothing."""
    widget = event.widget
    if widget.winfo_class() == "TCombobox":
        # The class binding this replaces; a no-op unless the list is open
        # (and while it is open its own listbox handles Escape first).
        widget.tk.call("ttk::combobox::Unpost", widget)
    focus_next(widget)
    return "break"


def focus_next(widget):
    """Move the keyboard focus to the control after `widget` in Tab order."""
    nxt = widget.tk_focusNext()
    if nxt is not None and nxt is not widget:
        nxt.focus_set()
    return nxt


def press_button(event):
    press(event.widget)
    return "break"


def press(widget):
    """Press a button (or toggle a checkbutton) the way Space does.

    Returns True when the press happened; False for a disabled widget or one
    that is not a button at all.
    """
    proc = _INVOKE.get(widget.winfo_class())
    if proc is None or str(widget.cget("state")) == "disabled":
        return False
    widget.tk.call(proc, widget)
    return True


def bind_mnemonics(window, mapping):
    """Alt+<letter> accelerators for one window.

    `mapping` maps a letter to a target widget. Alt+<letter> anywhere in
    `window` presses the target if it is a button / checkbutton (a no-op
    while it is disabled, like a click), or focuses it otherwise. Letters
    are case-insensitive and must be unique within the window. Nothing is
    drawn on the widgets: no underline cue (the user prefers the plain
    look), so the letters are documented per window in the README.
    """
    targets = {}
    for letter, target in mapping.items():
        letter = letter.lower()
        if letter in targets:
            raise ValueError(f"duplicate mnemonic {letter!r} in {window}")
        targets[letter] = target

    def on_alt(event):
        target = targets.get(event.keysym.lower())
        if target is None or not target.winfo_exists():
            return None
        if target.winfo_class() in _INVOKE:
            press(target)
        else:
            target.focus_set()
        return "break"

    window.bind("<Alt-KeyPress>", on_alt)
    return targets


def link_embedded_checkbuttons(text_widget, checkbuttons):
    """Keyboard traversal for checkbuttons embedded in a scrolled Text.

    `tk_focusNext` skips a widget that is scrolled out of view (Tk unmaps it),
    so plain Tab would dead-end at the last visible checkbutton. Tab / Down
    step to the next one and Shift+Tab / Up to the previous, wrapping at the
    ends, each scrolling its target into view before focusing it.
    """
    def step(index, delta):
        target = checkbuttons[(index + delta) % len(checkbuttons)]
        text_widget.see(target)
        text_widget.update_idletasks()  # map the freshly scrolled-in window
        target.focus_set()
        return "break"

    for i, cb in enumerate(checkbuttons):
        for seq in ("<Tab>", "<Down>"):
            cb.bind(seq, lambda e, i=i: step(i, 1))
        for seq in ("<Shift-Tab>", "<Up>"):
            cb.bind(seq, lambda e, i=i: step(i, -1))
