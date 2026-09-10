"""Keyboard operation of MyAgent's windows (2026-09-10): the mouse is optional.

Every control is already a Tab stop (Tk's default traversal), so this module
adds only the three things Tk leaves out, plus one workaround:

* **A safe way out of a text box.** Inside a Text widget Tab TYPES a tab, so
  Escape moves the keyboard focus to the next control instead. It never
  inserts, deletes, submits or closes anything. Ctrl+Tab / Ctrl+Shift+Tab (Tk's
  own Text bindings) do the same forwards / backwards. The same Escape leaves
  an Entry, a Spinbox and a Combobox too, so "Escape = leave this field" holds
  everywhere. Installed ONCE per Tk instance as class bindings
  (`install_class_bindings`), so every field in every window, including ones
  added later, behaves alike.
* **Return presses the focused button** (Tk only had Space), through the same
  `tk::ButtonInvoke` Space uses, so it flashes exactly like a click.
* **Alt+letter mnemonics** (`bind_mnemonics`): the letter is underlined on the
  button or on the label naming a field, and Alt+<letter> anywhere in that
  window presses the button or focuses the field.
* `link_embedded_checkbuttons`: the Safety dialog keeps its checkbuttons inside
  a scrolled Text, and Tk's Tab traversal skips a widget scrolled out of view,
  so Tab / Shift+Tab / Down / Up step through them and scroll each into view.

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

_FIELD_CLASSES = ("Text", "Entry", "Spinbox", "TCombobox")


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

    `mapping` maps a letter to a target widget, or to a `(label, target)` pair
    for a field named by a separate Label. The letter is underlined in the
    target's text (the label's, for a pair): the first matching character,
    case-insensitive; a widget with no such text is bound without a cue.
    Alt+<letter> anywhere in `window` then presses the target if it is a
    button / checkbutton (a no-op while it is disabled, like a click), or
    focuses it otherwise. Letters are case-insensitive and must be unique
    within the window.
    """
    targets = {}
    for letter, spec in mapping.items():
        letter = letter.lower()
        if letter in targets:
            raise ValueError(f"duplicate mnemonic {letter!r} in {window}")
        label, target = spec if isinstance(spec, tuple) else (spec, spec)
        _underline(label, letter)
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


def _underline(widget, letter):
    # Only a widget that can show an underline (button, checkbutton, label)
    # gets one. Probing -text alone is not enough: Tk accepts unique option
    # abbreviations, so an entry or combobox answers "-text" with its
    # -textvariable and would then choke on -underline.
    if "underline" not in widget.keys():
        return
    index = str(widget.cget("text")).lower().find(letter)
    if index >= 0:
        widget.configure(underline=index)


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
