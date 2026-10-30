"""The sectioned list two editors show (2026-10-31): the Instruction Editor's
INSTRUCTIONS list (2026-09-11) and the Skills Manager's SKILLS list — one Tk
widget controller over the pure sections-and-order model in
instruction_layout.py.

A `SectionedList` is a ttk.Treeview under a fixed title band
(ui_mixin.list_title_band) with its scrollbar and the ▲ / ▼ / Section…
buttons: one header row per section (bold on a grey band; the pseudo-section
*Unfiled* italic, always last), its pages beneath with zebra rows, row ids
"s:<section>" / "p:<name>". It knows nothing about what a page IS. The host
gives it

    load()            the name-keyed store the layout keys live on
    save(store)       persist it after a move / drop / Section… edit
    collapsed()       the per-machine set of folded sections (agent_state.json)
    parent()          the dialog, for the Section… prompt and the warnings
    place_window      the host's geometry helper, for the prompt
    on_select(event)  <<TreeviewSelect>>: the host loads the selected page
    on_return()       Enter on a page: the host puts the focus in its text
    row_text / row_tags   decorate a page's row (the Skills list's mode
                      prefix and colour); a page's id stays its name

and the gestures — ▲ / ▼ and Alt+Up / Alt+Down, drag-and-drop, the Section…
prompt, the folded-section memory — are the class's, identical in both
lists. The Instructions list's contract is the **Instructions list**
paragraph of .claude/rules/CLAUDE_MYAGENT.md (tests/test_instruction_list.py,
through the mixin's delegates); the Skills list's is tests/test_skills_list.py.

The module-level functions take the tree explicitly so a bare test host
holding only a Treeview can drive them — which is how the Instructions
mixin's pinned helpers (`_select_instruction_row` & co.) are written.
"""

import tkinter as tk
from tkinter import font as tkfont, messagebox, ttk

from myagent.constants import INSTR_TREE_STYLE, LIST_BAND_BG, LIST_ZEBRA_BG
from myagent.instruction_layout import (
    UNFILED, UNFILED_LABEL, drop, file_page, layout, move_page, move_section,
    normalize_section, rename_section, section_of, sections,
)
from myagent.keyboard import bind_mnemonics
from myagent.ui_mixin import SMALL_BUTTON_FONT, list_title_band


def row_of(iid):
    """("page", name) or ("section", section) for a list row id."""
    return ("page", iid[2:]) if iid.startswith("p:") else ("section", iid[2:])


def row_iid(row):
    """The list row id of ("page", name) / ("section", section)."""
    kind, key = row
    return f"{'p' if kind == 'page' else 's'}:{key}"


def populate(tree, store, collapsed=(), row_text=None, row_tags=None):
    """Rebuild the list from the store: a header per section (id
    "s:<section>", the unfiled pseudo-section's is "s:"), its pages beneath
    (id "p:<name>"), in instruction_layout order; sections named in
    `collapsed` start closed. A page's row shows `row_text(name, entry)`
    (the name alone without it) and carries `row_tags(name, entry)` after
    the zebra tag."""
    tree.delete(*tree.get_children(""))
    for section, names in layout(store):
        header = tree.insert(
            "", "end", iid=f"s:{section}", text=section or UNFILED_LABEL,
            open=(section not in collapsed),
            tags=("section",) if section else ("unfiled",))
        for k, name in enumerate(names):
            entry = store.get(name)
            tags = ("odd",) if k % 2 else ()
            if row_tags is not None:
                tags = tags + tuple(row_tags(name, entry))
            tree.insert(header, "end", iid=f"p:{name}",
                        text=row_text(name, entry) if row_text is not None else name,
                        tags=tags)


def tree_alive(tree):
    """Whether `tree` is a live widget (None and a destroyed one are not)."""
    try:
        return tree is not None and bool(tree.winfo_exists())
    except tk.TclError:
        return False


def selected_row(tree):
    """The selected list row as ("page", name) / ("section", section), or None."""
    try:
        sel = tree.selection()
    except (AttributeError, tk.TclError):
        return None
    return row_of(sel[0]) if sel else None


def selected_name(tree):
    """The selected page's name, or "" (nothing, or a section header, selected)."""
    row = selected_row(tree)
    return row[1] if row and row[0] == "page" else ""


def select_row(tree, row, collapsed):
    """Select the list row `row` (("page", name) / ("section", section)),
    scrolled into view — opening its section if that was collapsed, and
    taking the section out of `collapsed`, so the next rebuild does not fold
    it back over its own selection — or clear the selection when `row` is
    None or names no row."""
    iid = None
    if row:
        iid = row_iid(row)
        if not tree.exists(iid):
            iid = None
    if iid is None:
        if tree.selection():
            tree.selection_set(())
        return
    parent = tree.parent(iid)
    if parent:
        collapsed.discard(parent[2:])   # `see` opens it — keep the set true
    tree.selection_set(iid)
    tree.focus(iid)
    tree.see(iid)


def refresh(tree, store, collapsed, select=None, row_text=None, row_tags=None):
    """Rebuild the list from `store` and select `select` — a row — or, when
    None, whatever was selected before if it still exists."""
    if select is None:
        select = selected_row(tree)
    populate(tree, store, collapsed, row_text, row_tags)
    select_row(tree, select, collapsed)


def note_toggle(tree, collapsed, opened):
    """<<TreeviewOpen>> / <<TreeviewClose>>: remember the user's choice in
    `collapsed`. Tk sets the focus item to the toggled row before generating
    them; a page has no open state to remember."""
    try:
        iid = tree.focus()
    except (AttributeError, tk.TclError):
        return
    if iid.startswith("s:"):
        (collapsed.discard if opened else collapsed.add)(iid[2:])


def section_for_new(tree, store):
    """Where a newly SAVEd name is filed: the section of the selected page
    (or the selected section itself), else unfiled."""
    row = selected_row(tree)
    if row is None:
        return UNFILED
    kind, key = row
    return key if kind == "section" else section_of(store.get(key, {}))


def theme_colors(host):
    """(band, zebra): the section header bands' and the alternate rows'
    colours — Other Setup's shades of the field colour where the host has
    them (other_setup_mixin._theme_band / _theme_zebra), the constants on a
    bare host."""
    band = host._theme_band() if hasattr(host, "_theme_band") else LIST_BAND_BG
    zebra = host._theme_zebra() if hasattr(host, "_theme_zebra") else LIST_ZEBRA_BG
    return band, zebra


class SectionedList:
    """The widgets and gestures of one sectioned list, built inside `pane`
    (a Frame the caller has placed): the title band (row 0), the tree with
    its scrollbar (row 1 — the stretchy one; the scrollbar runs the full
    height beside band and tree) and the ▲ / ▼ / Section… buttons (row 2).
    The widgets the host wires by name are attributes: `band`, `tree`,
    `up_btn`, `down_btn`, `section_btn`."""

    def __init__(self, pane, *, title, noun, load, save, collapsed, parent, place_window,
                 on_select=None, on_return=None, row_text=None, row_tags=None,
                 colors=(LIST_BAND_BG, LIST_ZEBRA_BG)):
        self.noun = noun
        self.load, self.save, self.collapsed = load, save, collapsed
        self.parent, self.place_window = parent, place_window
        self.on_select, self.on_return = on_select, on_return
        self.row_text, self.row_tags = row_text, row_tags
        self._drag = None

        pane.grid_rowconfigure(1, weight=1)
        pane.grid_columnconfigure(0, weight=1)
        style = ttk.Style(pane)
        row_font = tkfont.Font(root=pane, font=("Arial", 10))
        # The title is a fixed band, not a ttk heading (since 2026-09-12; the
        # why is at ui_mixin.list_title_band). It covers the list proper only
        # — not the scrollbar, which runs the full height beside band and
        # tree (the first cut spanned the scrollbar too; the user asked for
        # the list's width the same day).
        self.band = list_title_band(pane, title)
        self.band.grid(row=0, column=0, sticky="ew")
        # One style for the one kind of list (its name predates the Skills
        # list): a shallow indent (Tk's default is 20 px) leaves long names
        # more room, and Other Setup's field-colour walk knows the lists by it
        style.configure(INSTR_TREE_STYLE, font=("Arial", 10), indent=10,
                        rowheight=row_font.metrics("linespace") + 6)
        tree = self.tree = ttk.Treeview(pane, show="tree", selectmode="browse",
                                        style=INSTR_TREE_STYLE)
        # Forty characters wide to begin with — the width of the Listbox the
        # Skills Manager had — so a pane added without a remembered width
        # (the Skills Manager's first opening) is as wide as before; a
        # Treeview's own natural width is 200 px, a third of that at 200 %.
        # A pane added WITH a width (the editor's) sizes the tree to itself.
        tree.column("#0", width=row_font.measure("0" * 40), stretch=True, minwidth=80)
        tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = tk.Scrollbar(pane, command=tree.yview)
        scrollbar.grid(row=0, column=1, rowspan=2, sticky="ns")
        tree.config(yscrollcommand=scrollbar.set)
        # Section headers are bold on a grey band, the Unfiled header italic
        # on the same band, and pages alternate white / off-white — the row
        # separation a Tk 8.6 Treeview has no rule lines for. Both colours
        # follow Other Setup's field colour (theme_colors).
        band_bg, zebra = colors
        tree.tag_configure("section", font=("Arial", 10, "bold"), background=band_bg)
        tree.tag_configure("unfiled", font=("Arial", 10, "italic"),
                           foreground="#555555", background=band_bg)
        tree.tag_configure("odd", background=zebra)

        btn_row = tk.Frame(pane)
        btn_row.grid(row=2, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self.up_btn = tk.Button(btn_row, text="▲", width=3, font=SMALL_BUTTON_FONT,
                                command=lambda: self.move(-1))
        self.up_btn.pack(side=tk.LEFT)
        self.down_btn = tk.Button(btn_row, text="▼", width=3, font=SMALL_BUTTON_FONT,
                                  command=lambda: self.move(1))
        self.down_btn.pack(side=tk.LEFT, padx=(3, 0))
        self.section_btn = tk.Button(btn_row, text="Section…", command=self.open_section_dialog,
                                     padx=6, font=SMALL_BUTTON_FONT)
        self.section_btn.pack(side=tk.LEFT, padx=(8, 0))

        if on_select is not None:
            tree.bind("<<TreeviewSelect>>", on_select)
        tree.bind("<<TreeviewOpen>>", lambda e: self.note_toggle(opened=True))
        tree.bind("<<TreeviewClose>>", lambda e: self.note_toggle(opened=False))
        for seq in ("<Return>", "<KP_Enter>"):
            tree.bind(seq, self._on_return)
        # Widget-level bindings run before the Treeview class ones and
        # break, so Alt+Up does not ALSO step the selection like a plain Up.
        tree.bind("<Alt-Up>", lambda e: (self.move(-1), "break")[1])
        tree.bind("<Alt-Down>", lambda e: (self.move(1), "break")[1])
        tree.bind("<ButtonPress-1>", self._on_press)
        tree.bind("<B1-Motion>", self._on_motion)
        tree.bind("<ButtonRelease-1>", self._on_release)

    # ── The host's view of the list ─────────────────────────────────────

    def refresh(self, store=None, select=None):
        """Rebuild the list (from `store`, else `load()`) and select `select`
        — a ("page", name) / ("section", section) row — or, when None,
        whatever was selected before if it still exists. Nothing happens
        once the tree is gone."""
        if not tree_alive(self.tree):
            return
        if store is None:
            store = self.load()
        refresh(self.tree, store, self.collapsed(), select, self.row_text, self.row_tags)

    def selected_row(self):
        return selected_row(self.tree)

    def selected_name(self):
        return selected_name(self.tree)

    def select(self, row):
        select_row(self.tree, row, self.collapsed())

    def section_for_new(self, store):
        return section_for_new(self.tree, store)

    def note_toggle(self, opened):
        note_toggle(self.tree, self.collapsed(), opened)

    # ── Gestures ────────────────────────────────────────────────────────

    def _on_return(self, event=None):
        """Enter on a page: it is already loaded (selection loads), so the
        host puts the focus in its text. On a section header Tk's own binding
        toggles it open / closed."""
        if not self.selected_name():
            return None
        if self.on_return is not None:
            self.on_return()
        return "break"

    def move(self, delta):
        """▲ / ▼, Alt+Up / Alt+Down: move the selected page (or section) one
        step — a page steps into the neighbouring section at its section's
        edge — and save the new order."""
        row = self.selected_row()
        if row is None:
            return
        kind, key = row
        store = self.load()
        moved = (move_page if kind == "page" else move_section)(store, key, delta)
        if moved:
            self.save(store)
            self.refresh(store, select=row)

    # Drag-and-drop within the list: press on a row, release on another.
    # The press still selects (the class binding runs after this one), so a
    # dragged page is loaded like a clicked one; a release on the row that
    # was pressed is a plain click, so a click never reorders anything.

    def _on_press(self, event):
        tree = self.tree
        iid = tree.identify_row(event.y)
        region = tree.identify_region(event.x, event.y)
        self._drag = iid if iid and region in ("tree", "cell") else None

    def _on_motion(self, event):
        if self._drag:
            over = self.tree.identify_row(event.y)
            self.tree.config(cursor="hand2" if over and over != self._drag else "")

    def _on_release(self, event):
        tree = self.tree
        source, self._drag = self._drag, None
        tree.config(cursor="")
        target = tree.identify_row(event.y) if source else ""
        if not target or target == source:
            return
        kind, key = row_of(source)
        target_kind, target_key = row_of(target)
        store = self.load()
        if drop(store, kind, key, target_kind, target_key):
            self.save(store)
            self.refresh(store, select=(kind, key))

    def open_section_dialog(self):
        """Section…: file the selected page into a section (an existing one,
        a new name, or Unfiled), or rename the selected section (onto an
        existing name = merge into it)."""
        win = self.parent()
        noun = self.noun
        row = self.selected_row()
        if row is None:
            article = "an" if noun[:1] in "aeiou" else "a"
            messagebox.showwarning(
                "No selection", f"Select {article} {noun} to move, or a section to rename.",
                parent=win)
            return
        kind, key = row
        store = self.load()
        choices = sections(store) + [UNFILED_LABEL]
        if kind == "page":
            if key not in store:
                return
            answer = self.ask_section(
                "Move to section",
                f"Section for '{key}' — pick one, or type a new name:",
                choices, section_of(store[key]) or UNFILED_LABEL)
            changed = answer is not None and file_page(store, key, answer)
            select = row
        else:
            if key == UNFILED:
                messagebox.showwarning(
                    UNFILED_LABEL,
                    f"'{UNFILED_LABEL}' holds the {noun}s not filed in any section "
                    f"and cannot be renamed — move its {noun}s into a section instead.",
                    parent=win)
                return
            answer = self.ask_section(
                "Rename section",
                f"New name for section '{key}' (an existing name merges into that section):",
                choices, key)
            changed = answer is not None and rename_section(store, key, answer)
            select = ("section", normalize_section(answer)) if changed else row
        if changed:
            self.save(store)
            self.refresh(store, select=select)

    def ask_section(self, title, prompt, choices, initial):
        """A small modal prompt over the editor: a label, an editable
        dropdown pre-filled with `initial` (existing sections and Unfiled to
        pick from, or type a new name), OK / Cancel. Returns the text, or
        None on Cancel. Enter is OK (on a button, that button), Escape
        cancels from anywhere — the one field here holds at most a section
        name, so unlike the editor there is no draft to protect; Alt+O /
        Alt+C press the buttons."""
        parent = self.parent()
        dlg = tk.Toplevel(parent)
        dlg.withdraw()
        dlg.title(title)
        dlg.transient(parent)
        dlg.resizable(False, False)
        result = [None]

        tk.Label(dlg, text=prompt, font=("Arial", 10), wraplength=360, justify="left").grid(
            row=0, column=0, sticky="w", padx=15, pady=(12, 4))
        var = tk.StringVar(value=initial)
        combo = ttk.Combobox(dlg, textvariable=var, values=choices, font=("Arial", 10), width=36)
        combo.grid(row=1, column=0, sticky="ew", padx=15, pady=(0, 10))

        def ok(event=None):
            result[0] = var.get()
            dlg.destroy()

        def cancel(event=None):
            dlg.destroy()
            return "break"

        btn_row = tk.Frame(dlg)
        btn_row.grid(row=2, column=0, pady=(0, 12))
        ok_btn = tk.Button(btn_row, text="OK", width=8, command=ok)
        ok_btn.pack(side=tk.LEFT, padx=8)
        cancel_btn = tk.Button(btn_row, text="Cancel", width=8, command=cancel)
        cancel_btn.pack(side=tk.LEFT, padx=8)
        dlg.protocol("WM_DELETE_WINDOW", cancel)
        dlg.bind("<Return>", ok)
        dlg.bind("<KP_Enter>", ok)
        dlg.bind("<Escape>", cancel)
        combo.bind("<Escape>", cancel)   # before the leave-the-field class binding
        bind_mnemonics(dlg, {"o": ok_btn, "c": cancel_btn})

        dlg.update_idletasks()
        self.place_window(dlg, "section_prompt",
                          (dlg.winfo_reqwidth(), dlg.winfo_reqheight()), parent=parent)
        dlg.deiconify()
        combo.focus_set()
        combo.selection_range(0, tk.END)
        dlg.wait_visibility()
        dlg.grab_set()
        dlg.wait_window()
        return result[0]
