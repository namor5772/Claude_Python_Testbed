"""The Skills Manager's SKILLS list as a sectioned tree (2026-10-31) — the
Instruction Editor's Instructions list applied to the skills: every skill a
page in a section (OneNote-style), selecting loads, ▲ / ▼ and Alt+Up /
Alt+Down move, a row can be dragged onto another, Section… files or renames,
folded sections persist per machine — through one shared widget controller,
`myagent/sectioned_list.py`, over the pure model in instruction_layout.py.

Pinned here:

* the two frontmatter keys the layout rides in (`section:` / `order:`,
  datapaths.SKILL_LAYOUT_KEYS): written after `mode:` only when set, read
  back as a string and an int, anything else dropped; an untouched skill's
  file is byte-identical to before; and a OneDrive fork — or a duplicate
  folder — that differs ONLY in them heals as an identical one (`_same_skill`)
  instead of materializing a '<name>__<Computer>' sibling skill;
* the layout model is store-agnostic: `move_page` & co. on a skills dict;
* `SectionedList` on a real Treeview (withdrawn root): the band and grid the
  Instructions list has (tests/test_instruction_list.py pins the editor's
  through the mixin's delegates), the row decoration hook the Skills list
  uses ([ON] / [OD] prefix + a mode tag over the zebra tag) while a row's id
  stays the bare name, the moves / drop / Section… edits saving through the
  host's callbacks, Enter on a page vs a header;
* the REAL `open_skills_editor` on a stub host, driven through its buttons:
  the tree in sections, a folded section remembered and re-opened by a
  selection, selecting loads the fields, Cycle Mode re-decorates the row and
  keeps an edit in progress, ▲ saves the new order, SAVE of a NEW name files
  it where the selection is, DELETE refuses a section header and removes a
  skill, Section… moves a skill, the close lets the list object go;
* agent_state.json: `skills_collapsed_sections`, one writer and one reader;
* wiring scans of skills_mixin.py / instructions_mixin.py / MyAgent.py.
"""

import copy
import pathlib
import tkinter as tk
import types
import unittest
from tkinter import font as tkfont, ttk
from unittest import mock

from myagent import datapaths as dp
from myagent import sectioned_list, skills_mixin
from myagent.constants import SKILLS_DIR
from myagent.instruction_layout import drop, file_page, layout, move_page, rows
from myagent.sectioned_list import SectionedList, populate, selected_name, selected_row
from myagent.skills_mixin import SkillsMixin
from tests._util import stub
from tests.test_skills_tree import SkillsTreeCase
from tests.test_state_skill_modes import _Host, _StateFileCase, GLOBAL_TREE

REPO = pathlib.Path(__file__).resolve().parents[1]


def _read(*parts):
    return (REPO / pathlib.Path(*parts)).read_text(encoding="utf-8")


def _skills():
    """A skills dict as _load_skills would return it, two sections and an
    unfiled skill — fictitious names, so nothing resolves into the real
    shared tree."""
    return {
        "bank-b": {"content": "body b", "mode": "on_demand", "section": "Bank", "order": 1,
                   "description": "Second bank skill."},
        "bank-a": {"content": "body a", "mode": "disabled", "section": "Bank", "order": 0},
        "mail-a": {"content": "body m", "mode": "enabled", "section": "Mail", "order": 2},
        "loose": {"content": "body l", "mode": "disabled"},
    }


# ── The frontmatter keys ─────────────────────────────────────────────────

class FrontmatterTests(SkillsTreeCase):

    def test_section_and_order_follow_mode_and_round_trip(self):
        text = dp._serialize_skill_md("bank-a", {"content": "body", "mode": "disabled",
                                                "section": "Bank", "order": 3})
        self.assertEqual(text, "---\nname: bank-a\nmode: disabled\nsection: Bank\norder: 3\n"
                               "---\n\nbody\n")
        name, entry = dp._entry_from_md(text, "fallback")
        self.assertEqual((name, entry), ("bank-a", {"content": "body", "mode": "disabled",
                                                     "section": "Bank", "order": 3}))

    def test_a_skill_nobody_filed_or_moved_keeps_its_old_file(self):
        before = "---\nname: x\ndescription: d\nmode: on_demand\n---\n\nbody\n"
        entry = {"content": "body", "mode": "on_demand", "description": "d"}
        self.assertEqual(dp._serialize_skill_md("x", entry), before)
        self.assertEqual(dp._entry_from_md(before, "x")[1], entry)      # no layout keys appear

    def test_only_a_real_section_and_an_int_order_are_written_or_read(self):
        for entry in ({"section": "", "order": None}, {"section": "   ", "order": True},
                      {"section": None, "order": "4"}, {"section": 7, "order": 2.0}):
            text = dp._serialize_skill_md("x", dict(entry, content="b", mode="disabled"))
            self.assertNotIn("section:", text, entry)
            self.assertNotIn("order:", text, entry)
        text = ("---\nname: x\nmode: disabled\nsection:   Two  Words \norder: three\n"
                "---\n\nb\n")
        _, entry = dp._entry_from_md(text, "x")
        self.assertEqual(entry.get("section"), "Two Words")    # whitespace collapsed
        self.assertNotIn("order", entry)                         # not an int: dropped
        _, entry = dp._entry_from_md("---\nname: x\nmode: disabled\norder: -2\n---\n\nb\n", "x")
        self.assertEqual(entry["order"], -2)

    def test_the_tree_round_trips_the_layout(self):
        skills = _skills()
        dp.save_skills_tree(str(self.tree), skills)
        self.assertEqual(dp.load_skills_tree(str(self.tree)), skills)
        # ... and a diff-aware save after a move rewrites exactly the files whose
        # frontmatter changed: the two swapped rows, and the until-then unordered
        # skill the renumber gives its number (instruction_layout.renumber
        # writes every page's order) — mail-a keeps its 2 and its file
        before = {sub: self.md_path(sub).read_text(encoding="utf-8") for sub in skills}
        move_page(skills, "bank-b", -1)       # swaps with bank-a
        dp.save_skills_tree(str(self.tree), skills)
        changed = {sub for sub in skills
                   if self.md_path(sub).read_text(encoding="utf-8") != before[sub]}
        self.assertEqual(changed, {"bank-a", "bank-b", "loose"})
        self.assertEqual(skills["loose"]["order"], 3)
        self.assertEqual([n for _, n in rows(dp.load_skills_tree(str(self.tree)))],
                         ["bank-b", "bank-a", "mail-a", "loose"])

    def test_same_skill_sets_the_layout_keys_aside(self):
        a = {"content": "c", "mode": "disabled", "section": "Bank", "order": 1}
        self.assertTrue(dp._same_skill(a, {"content": "c", "mode": "disabled"}))
        self.assertTrue(dp._same_skill(a, dict(a, order=9, section="Mail")))
        self.assertFalse(dp._same_skill(a, dict(a, content="other")))
        self.assertFalse(dp._same_skill(a, dict(a, mode="enabled")))
        self.assertFalse(dp._same_skill(a, None))
        self.assertEqual(dp.SKILL_LAYOUT_KEYS, ("section", "order"))

    def test_a_fork_differing_only_in_placement_heals_as_identical(self):
        mine = {"content": "same", "mode": "disabled", "section": "Bank", "order": 0}
        self.write_md("S", dp._serialize_skill_md("S", mine))
        fork = self.write_md("S", dp._serialize_skill_md("S", dict(mine, order=5, section="Mail")),
                             basename="SKILL-Mac-mini.md")
        loaded = dp.load_skills_tree(str(self.tree))
        self.assertEqual(list(loaded), ["S"])                  # no S__Mac-mini
        self.assertEqual(loaded["S"], mine)                     # the main file's placement stays
        self.assertFalse(fork.exists())

    def test_a_fork_with_other_content_still_materializes_a_variant(self):
        self.write_md("S", dp._serialize_skill_md("S", {"content": "mine", "mode": "disabled",
                                                       "order": 0}))
        self.write_md("S", dp._serialize_skill_md("S", {"content": "theirs", "mode": "disabled",
                                                       "order": 0}),
                      basename="SKILL-Mac-mini.md")
        loaded = dp.load_skills_tree(str(self.tree))
        self.assertEqual(loaded["S"]["content"], "mine")
        self.assertEqual(loaded["S__Mac-mini"]["content"], "theirs")

    def test_a_duplicate_folder_differing_only_in_placement_is_junk(self):
        entry = {"content": "one", "mode": "disabled", "order": 0}
        self.write_md("Same", dp._serialize_skill_md("Same", entry))
        self.write_md("Same_2", dp._serialize_skill_md("Same", dict(entry, order=3)))
        loaded = dp.load_skills_tree(str(self.tree))
        self.assertEqual(list(loaded), ["Same"])
        self.assertFalse((self.tree / "Same_2").exists())


# ── The model over a skills dict ──────────────────────────────────────────

class LayoutOnSkillsTests(unittest.TestCase):

    def test_the_layout_functions_read_a_skills_dict_like_an_instruction_store(self):
        skills = _skills()
        self.assertEqual(layout(skills), [("Bank", ["bank-a", "bank-b"]), ("Mail", ["mail-a"]),
                                          ("", ["loose"])])
        self.assertTrue(file_page(skills, "loose", "Mail"))
        self.assertEqual(skills["loose"]["section"], "Mail")
        self.assertEqual([n for _, n in rows(skills)], ["bank-a", "bank-b", "mail-a", "loose"])
        self.assertTrue(drop(skills, "page", "loose", "section", "Bank"))
        self.assertEqual(layout(skills)[0], ("Bank", ["loose", "bank-a", "bank-b"]))
        # The skill's own fields are untouched by every edit
        self.assertEqual(skills["loose"]["content"], "body l")
        self.assertEqual(skills["bank-b"]["description"], "Second bank skill.")


# ── The shared widget ─────────────────────────────────────────────────────

def _row_text(name, entry):
    mode = (entry or {}).get("mode", "disabled")
    return {"enabled": "[ON] ", "on_demand": "[OD] "}.get(mode, "     ") + name


def _row_tags(name, entry):
    mode = (entry or {}).get("mode", "disabled")
    return (mode,) if mode in ("enabled", "on_demand") else ()


class _TkCase(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()


class PopulateTests(_TkCase):

    def test_decorated_rows_keep_the_bare_name_as_their_id(self):
        tree = ttk.Treeview(self.root, show="tree")
        populate(tree, _skills(), row_text=_row_text, row_tags=_row_tags)
        self.assertEqual(tree.get_children(""), ("s:Bank", "s:Mail", "s:"))
        self.assertEqual(tree.get_children("s:Bank"), ("p:bank-a", "p:bank-b"))
        self.assertEqual(tree.item("p:bank-a", "text"), "     bank-a")
        self.assertEqual(tree.item("p:bank-b", "text"), "[OD] bank-b")
        self.assertEqual(tree.item("p:mail-a", "text"), "[ON] mail-a")
        self.assertEqual(tuple(tree.item("p:bank-a", "tags")), ())
        self.assertEqual(tuple(tree.item("p:bank-b", "tags")), ("odd", "on_demand"))   # zebra first
        self.assertEqual(tuple(tree.item("p:mail-a", "tags")), ("enabled",))
        tree.selection_set("p:bank-b")
        self.assertEqual(selected_row(tree), ("page", "bank-b"))
        self.assertEqual(selected_name(tree), "bank-b")
        # Without the hooks a row is its name, as the Instructions list has it
        populate(tree, _skills())
        self.assertEqual(tree.item("p:bank-b", "text"), "bank-b")
        self.assertEqual(tuple(tree.item("p:bank-b", "tags")), ("odd",))


class _ListHost:
    """The callbacks a SectionedList needs, recording what it saves."""

    def __init__(self, store):
        self.store = store
        self.saves = []
        self.collapsed_set = set()
        self.returns = 0

    def load(self):
        return self.store

    def save(self, store):
        self.saves.append(copy.deepcopy(store))

    def collapsed(self):
        return self.collapsed_set

    def on_return(self):
        self.returns += 1


class SectionedListTests(_TkCase):

    def setUp(self):
        super().setUp()
        self.pane = tk.Frame(self.root)
        self.pane.pack()
        self.host = _ListHost(_skills())
        self.lst = SectionedList(
            self.pane, title="Skills", noun="skill",
            load=self.host.load, save=self.host.save, collapsed=self.host.collapsed,
            parent=lambda: self.root, place_window=lambda *a, **k: None,
            on_return=self.host.on_return, row_text=_row_text, row_tags=_row_tags)
        self.lst.refresh()

    def test_the_band_tree_scrollbar_and_buttons_are_gridded_as_the_instructions_list(self):
        lst, pane = self.lst, self.pane
        self.assertEqual(lst.band.cget("text"), "SKILLS")
        self.assertEqual(lst.band.cget("background"), "#bcdcf4")
        self.assertEqual((int(lst.band.grid_info()["row"]), int(lst.band.grid_info()["column"])),
                         (0, 0))
        self.assertEqual(int(lst.tree.grid_info()["row"]), 1)
        show = tuple(str(s) for s in self.root.tk.splitlist(lst.tree.cget("show")))
        self.assertEqual(show, ("tree",))
        self.assertEqual(str(lst.tree.cget("style")), "Instr.Treeview")    # the one list style
        # As wide as the 40-column Listbox it replaced, so a first opening
        # (no remembered sash) shows whole names, not a 200-px Treeview
        forty = tkfont.Font(root=self.root, font=("Arial", 10)).measure("0" * 40)
        self.assertEqual(int(lst.tree.column("#0", "width")), forty)
        self.assertGreater(forty, 200)
        (scrollbar,) = pane.grid_slaves(row=0, column=1)
        self.assertIsInstance(scrollbar, tk.Scrollbar)
        self.assertEqual(int(scrollbar.grid_info()["rowspan"]), 2)
        self.assertEqual(int(pane.grid_rowconfigure(1)["weight"]), 1)
        (btn_row,) = pane.grid_slaves(row=2, column=0)
        self.assertEqual([b.cget("text") for b in btn_row.pack_slaves()], ["▲", "▼", "Section…"])
        self.assertIs(btn_row.pack_slaves()[2], lst.section_btn)
        from myagent.keyboard import list_move_sequences      # Alt+Up / Down; Cmd+ on the Mac
        for seq in (*list_move_sequences(lst.tree), "<Return>", "<KP_Enter>", "<ButtonPress-1>",
                    "<B1-Motion>", "<ButtonRelease-1>", "<<TreeviewOpen>>", "<<TreeviewClose>>"):
            self.assertTrue(lst.tree.bind(seq), seq)
        self.assertFalse(lst.tree.bind("<<TreeviewSelect>>"))     # none given: nothing bound

    def test_move_saves_the_new_order_and_keeps_the_row_selected(self):
        lst, host = self.lst, self.host
        lst.select(("page", "bank-b"))
        lst.up_btn.invoke()
        self.assertEqual(lst.tree.get_children("s:Bank"), ("p:bank-b", "p:bank-a"))
        self.assertEqual(lst.selected_row(), ("page", "bank-b"))
        self.assertEqual(len(host.saves), 1)
        self.assertEqual(host.saves[0]["bank-b"]["order"], 0)
        lst.up_btn.invoke()                        # already first: nothing to save
        self.assertEqual(len(host.saves), 1)
        lst.select(("section", "Mail"))
        lst.up_btn.invoke()                        # a section moves as a block
        self.assertEqual(lst.tree.get_children(""), ("s:Mail", "s:Bank", "s:"))
        self.assertEqual(lst.selected_row(), ("section", "Mail"))
        self.assertEqual(len(host.saves), 2)

    def test_a_drop_lands_the_dragged_row_and_saves(self):
        lst, host = self.lst, self.host
        at = {10: "p:loose", 30: "s:Bank", 50: "p:bank-b"}
        lst.tree.identify_row = lambda y: at.get(y, "")
        lst.tree.identify_region = lambda x, y: "tree"
        lst._on_press(types.SimpleNamespace(x=5, y=10))
        self.assertEqual(lst._drag, "p:loose")
        lst._on_release(types.SimpleNamespace(x=5, y=10))      # released where pressed: a click
        self.assertEqual(host.saves, [])
        lst._on_press(types.SimpleNamespace(x=5, y=10))
        lst._on_release(types.SimpleNamespace(x=5, y=30))      # onto the header: first page
        self.assertEqual(lst.tree.get_children("s:Bank"), ("p:loose", "p:bank-a", "p:bank-b"))
        self.assertEqual(lst.selected_row(), ("page", "loose"))
        self.assertEqual(host.saves[-1]["loose"]["section"], "Bank")
        self.assertIsNone(lst._drag)
        lst._on_press(types.SimpleNamespace(x=5, y=99))        # pressed on nothing: no drag
        self.assertIsNone(lst._drag)

    def test_section_dialog_files_a_page_and_renames_a_section(self):
        lst, host = self.lst, self.host
        lst.select(("page", "loose"))
        with mock.patch.object(SectionedList, "ask_section", return_value="Mail") as ask:
            lst.open_section_dialog()
        self.assertEqual(ask.call_args.args[0], "Move to section")
        self.assertEqual(ask.call_args.args[2], ["Bank", "Mail", "Unfiled"])
        self.assertEqual(lst.tree.get_children("s:Mail"), ("p:mail-a", "p:loose"))
        self.assertNotIn("s:", lst.tree.get_children(""))             # nothing unfiled is left
        self.assertEqual(host.saves[-1]["loose"]["section"], "Mail")
        lst.select(("section", "Mail"))
        with mock.patch.object(SectionedList, "ask_section", return_value="Post") as ask:
            lst.open_section_dialog()
        self.assertEqual(ask.call_args.args[0], "Rename section")
        self.assertEqual(lst.tree.get_children(""), ("s:Bank", "s:Post"))
        self.assertEqual(lst.selected_row(), ("section", "Post"))
        with mock.patch.object(SectionedList, "ask_section", return_value=None):
            lst.open_section_dialog()                                  # Cancel: nothing
        self.assertEqual(len(host.saves), 2)
        lst.select(None)
        with mock.patch.object(sectioned_list.messagebox, "showwarning") as warn:
            lst.open_section_dialog()
        self.assertIn("Select a skill to move", warn.call_args.args[1])  # the noun, its article

    def test_enter_on_a_page_calls_the_host_and_breaks_on_a_header_it_leaves_tk_its_toggle(self):
        lst, host = self.lst, self.host
        lst.select(("page", "bank-a"))
        self.assertEqual(lst._on_return(), "break")
        self.assertEqual(host.returns, 1)
        lst.select(("section", "Bank"))
        self.assertIsNone(lst._on_return())
        self.assertEqual(host.returns, 1)

    def test_a_folded_section_is_remembered_and_reopened_by_a_selection(self):
        lst, host = self.lst, self.host
        lst.tree.focus("s:Bank")
        lst.note_toggle(opened=False)
        self.assertEqual(host.collapsed_set, {"Bank"})
        lst.refresh()
        self.assertFalse(lst.tree.item("s:Bank", "open"))
        lst.select(("page", "bank-a"))
        self.assertTrue(lst.tree.item("s:Bank", "open"))
        self.assertEqual(host.collapsed_set, set())
        self.assertEqual(lst.section_for_new(host.store), "Bank")
        lst.select(None)
        self.assertEqual(lst.section_for_new(host.store), "")
        lst.tree.destroy()
        lst.refresh()                                   # a gone tree: nothing, no error


# ── The real Skills Manager ───────────────────────────────────────────────

class _Button:
    text = None

    def config(self, **kw):
        self.text = kw.get("text", self.text)


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _find(win, cls, **match):
    for w in _walk(win):
        if w.winfo_class() == cls and all(str(w.cget(k)) == v for k, v in match.items()):
            return w
    raise AssertionError(f"no {cls} {match} in the dialog")


class DialogTests(_TkCase):
    """The REAL open_skills_editor on a stub host: a Toplevel, transient of
    the withdrawn root, so it never maps — its widgets and bindings work all
    the same. Everything that would touch the shared tree (_save_skills,
    delete_skill_tree_entry, copy_skill_resources) is stubbed or mocked."""

    def setUp(self):
        super().setUp()
        skills = _skills()
        self.saves = []
        host = self.host = stub(
            SkillsMixin, root=self.root, skills=skills,
            _skill_global_modes={n: e["mode"] for n, e in skills.items()},
            skills_editor_window=None, instruction_editor_window=None,
            _skills_refresh_list=None, _skills_list_width=None,
            _skills_collapsed_sections={"Mail"}, skills_button=_Button())
        host._remember_geometry = lambda kind, win: None
        host._save_last_state = lambda: None
        host._place_window = lambda *a, **k: None
        host._save_skills = lambda: self.saves.append(copy.deepcopy(host.skills))
        host.open_skills_editor()
        self.win = host.skills_editor_window
        self.lst = host._skills_list
        self.tree = self.lst.tree
        self.name_entry = _find(self.win, "Entry")
        # The skill text: the window's one text box since the description
        # moved into the Description dialog (2026-10-31, tests/test_skill_description.py)
        self.text = _find(self.win, "Text")
        self.buttons = {t: _find(self.win, "Button", text=t)
                        for t in ("SAVE", "DELETE", "NEW", "Cycle Mode", "Description")}

    def pump(self):
        self.root.update()

    def test_the_list_shows_the_skills_in_sections_and_remembers_a_folded_one(self):
        tree = self.tree
        self.assertEqual(tree.get_children(""), ("s:Bank", "s:Mail", "s:"))
        self.assertEqual(tree.get_children("s:Bank"), ("p:bank-a", "p:bank-b"))
        self.assertEqual(tree.item("p:bank-b", "text"), "[OD] bank-b")
        self.assertEqual(tuple(tree.item("p:mail-a", "tags")), ("enabled",))
        self.assertEqual(str(tree.tag_configure("enabled", "foreground")), "#2e7d32")
        self.assertEqual(str(tree.tag_configure("on_demand", "foreground")), "#1565c0")
        self.assertFalse(tree.item("s:Mail", "open"))           # folded last time
        self.assertTrue(tree.bind("<space>"))                    # Space cycles the mode
        self.assertIs(self.host._skills_refresh_list.__closure__ is not None, True)
        self.lst.select(("page", "mail-a"))
        self.assertTrue(tree.item("s:Mail", "open"))
        self.assertEqual(self.host._skills_collapsed_sections, set())

    def test_selecting_a_skill_loads_it_and_cycle_mode_keeps_an_edit_in_progress(self):
        self.lst.select(("page", "bank-b"))
        self.pump()
        self.assertEqual(self.name_entry.get(), "bank-b")
        self.assertEqual(self.host._desc_draft(), "Second bank skill.")    # behind the Description button
        self.assertEqual(self.text.get("1.0", "end-1c"), "body b")
        self.text.insert("end", " DRAFT")
        self.buttons["Cycle Mode"].invoke()                       # on_demand → disabled
        self.pump()
        self.assertEqual(self.host.skills["bank-b"]["mode"], "disabled")
        self.assertEqual(self.host._skill_global_modes["bank-b"], "disabled")
        self.assertEqual(len(self.saves), 1)
        self.assertEqual(self.tree.item("p:bank-b", "text"), "     bank-b")
        self.assertEqual(tuple(self.tree.item("p:bank-b", "tags")), ("odd",))
        self.assertEqual(self.lst.selected_row(), ("page", "bank-b"))
        self.assertEqual(self.text.get("1.0", "end-1c"), "body b DRAFT")   # not reloaded
        self.assertEqual(self.host.skills_button.text, "Skills (1)")
        # Selecting a header loads nothing and Cycle Mode on it asks for a skill
        self.lst.select(("section", "Bank"))
        self.pump()
        self.assertEqual(self.name_entry.get(), "bank-b")
        with mock.patch.object(skills_mixin.messagebox, "showwarning") as warn:
            self.buttons["Cycle Mode"].invoke()
        self.assertEqual(warn.call_args.args[0], "No selection")

    def test_the_arrows_save_the_new_order(self):
        self.lst.select(("page", "bank-b"))
        self.pump()
        self.lst.up_btn.invoke()
        self.assertEqual(self.tree.get_children("s:Bank"), ("p:bank-b", "p:bank-a"))
        self.assertEqual((self.host.skills["bank-b"]["order"], self.host.skills["bank-a"]["order"]),
                         (0, 1))
        self.assertEqual(len(self.saves), 1)
        self.lst.down_btn.invoke()
        self.assertEqual(self.tree.get_children("s:Bank"), ("p:bank-a", "p:bank-b"))
        self.assertEqual(len(self.saves), 2)
        self.pump()
        self.assertEqual(self.name_entry.get(), "bank-b")        # still the loaded skill

    def test_save_files_a_new_skill_where_the_selection_is_and_keeps_an_existing_one(self):
        self.lst.select(("page", "bank-a"))
        self.pump()
        self.name_entry.delete(0, tk.END)
        self.name_entry.insert(0, "bank-new")
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", "new body")
        with mock.patch.object(skills_mixin, "copy_skill_resources", return_value=[]) as cp:
            self.buttons["SAVE"].invoke()
        cp.assert_called_once_with(SKILLS_DIR, "bank-a", "bank-new")   # the save-as source
        new = self.host.skills["bank-new"]
        self.assertEqual((new["content"], new["mode"], new["section"]),
                         ("new body", "disabled", "Bank"))
        self.assertEqual(self.tree.get_children("s:Bank"), ("p:bank-a", "p:bank-b", "p:bank-new"))
        self.assertEqual([n for _, n in rows(self.host.skills)],
                         ["bank-a", "bank-b", "bank-new", "mail-a", "loose"])
        self.assertEqual(self.lst.selected_row(), ("page", "bank-new"))
        self.assertEqual(len(self.saves), 1)
        self.pump()
        self.assertEqual(self.text.get("1.0", "end-1c"), "new body")
        # Re-saving an existing skill keeps its place
        self.text.insert("end", " more")
        self.buttons["SAVE"].invoke()
        self.assertEqual(self.host.skills["bank-new"]["content"], "new body more")
        self.assertEqual(self.host.skills["bank-new"]["section"], "Bank")
        self.assertEqual(self.tree.get_children("s:Bank"), ("p:bank-a", "p:bank-b", "p:bank-new"))
        # NEW with nothing selected: a new name lands unfiled
        self.buttons["NEW"].invoke()
        self.assertEqual(self.lst.selected_row(), None)
        self.name_entry.insert(0, "free-skill")
        self.text.insert("1.0", "free body")
        with mock.patch.object(skills_mixin, "copy_skill_resources", return_value=[]) as cp:
            self.buttons["SAVE"].invoke()
        cp.assert_not_called()                                    # no loaded source: no save-as
        self.assertNotIn("section", self.host.skills["free-skill"])
        self.assertEqual(self.tree.get_children("s:"), ("p:loose", "p:free-skill"))

    def test_delete_refuses_a_section_header_and_removes_a_skill(self):
        self.lst.select(("section", "Bank"))
        with mock.patch.object(skills_mixin.messagebox, "showwarning") as warn, \
                mock.patch.object(skills_mixin.messagebox, "askyesno") as ask:
            self.buttons["DELETE"].invoke()
        self.assertEqual(warn.call_args.args[0], "Section selected")
        ask.assert_not_called()
        self.assertIn("bank-a", self.host.skills)
        self.lst.select(("page", "loose"))
        self.pump()
        with mock.patch.object(skills_mixin.messagebox, "askyesno", return_value=True) as ask, \
                mock.patch.object(skills_mixin, "delete_skill_tree_entry") as remove:
            self.buttons["DELETE"].invoke()
        self.assertEqual(ask.call_args.kwargs.get("default"), "no")
        remove.assert_called_once_with(SKILLS_DIR, "loose")
        self.assertNotIn("loose", self.host.skills)
        self.assertNotIn("s:", self.tree.get_children(""))
        self.assertEqual(self.lst.selected_row(), None)
        self.assertEqual(self.name_entry.get(), "")
        self.assertEqual(len(self.saves), 1)

    def test_section_button_moves_a_skill(self):
        self.lst.select(("page", "bank-a"))
        self.pump()
        with mock.patch.object(SectionedList, "ask_section", return_value="Mail"):
            self.lst.section_btn.invoke()
        self.assertEqual(self.host.skills["bank-a"]["section"], "Mail")
        self.assertEqual(self.tree.get_children("s:Mail"), ("p:mail-a", "p:bank-a"))
        self.assertEqual(self.lst.selected_row(), ("page", "bank-a"))
        self.assertEqual(len(self.saves), 1)

    def test_the_close_lets_the_list_go(self):
        self.host._skills_list_pane = object()     # what the dialog set, stood in for
        self.root.tk.call(self.win.protocol("WM_DELETE_WINDOW"))
        self.assertFalse(self.win.winfo_exists())
        self.assertIsNone(self.host._skills_list)
        self.assertIsNone(self.host._skills_list_pane)


# ── The state file ────────────────────────────────────────────────────────

class StateFileTests(_StateFileCase):

    def test_the_folded_sections_round_trip_per_machine(self):
        first = _Host(GLOBAL_TREE, self.state_file)
        first._skills_collapsed_sections = {"Mail", "Bank"}
        first._save_last_state()
        self.assertEqual(self.read_state()["skills_collapsed_sections"], ["Bank", "Mail"])
        second = _Host(GLOBAL_TREE, self.state_file)
        second._load_last_state()
        self.assertEqual(second._skills_collapsed_sections, {"Bank", "Mail"})
        # Nothing folded, and a file from before the key, both read as none
        third = _Host(GLOBAL_TREE, self.state_file)
        third._save_last_state()
        self.assertEqual(self.read_state()["skills_collapsed_sections"], [])
        self.write_legacy_state()
        fourth = _Host(GLOBAL_TREE, self.state_file)
        fourth._load_last_state()
        self.assertEqual(fourth._skills_collapsed_sections, set())

    def test_one_writer_and_one_reader(self):
        src = _read("myagent", "state_mixin.py")
        save = src[src.index("def _save_last_state("):src.index("def _load_last_state(")]
        load = src[src.index("def _load_last_state("):src.index("def _periodic_save(")]
        self.assertEqual(src.count('"skills_collapsed_sections"'), 2)
        self.assertIn('state["skills_collapsed_sections"] = sorted(', save)
        self.assertIn('state.get("skills_collapsed_sections")', load)
        # Beside the Skills list's other key, after the editor's twin
        self.assertLess(save.index('"skills_list_width"'), save.index('"skills_collapsed_sections"'))
        self.assertLess(load.index('"collapsed_sections"'), load.index('"skills_collapsed_sections"'))


# ── Wiring ────────────────────────────────────────────────────────────────

class WiringTests(unittest.TestCase):

    def test_the_skills_manager_builds_a_sectioned_list_over_self_skills(self):
        src = _read("myagent", "skills_mixin.py")
        body = src[src.index("def open_skills_editor"):]
        self.assertIn("self._skills_list = skills_list = SectionedList(", body)
        self.assertIn('list_frame, title="SKILLS", noun="skill",', body)
        self.assertIn("load=lambda: self.skills, save=lambda store: self._save_skills(),", body)
        self.assertIn("collapsed=self._skills_collapsed, parent=lambda: win,", body)
        self.assertIn("on_select=on_select, on_return=focus_text,", body)
        self.assertIn("row_text=row_text, row_tags=row_tags, colors=theme_colors(self))", body)
        self.assertIn('skills_list.tree.tag_configure("enabled", foreground="#2e7d32")', body)
        self.assertIn('skills_list.tree.tag_configure("on_demand", foreground="#1565c0")', body)
        self.assertIn('skills_list.tree.bind("<space>", _cycle_on_space)', body)
        # The selection loads, the loaded skill is skipped (the queued-event rule)
        self.assertIn('if not name or name == loaded["name"] or name not in self.skills:', body)
        # SAVE files a new name where the selection is, then selects it
        self.assertIn("section = skills_list.section_for_new(self.skills)", body)
        self.assertIn("renumber(self.skills, rows(self.skills))", body)
        self.assertIn('refresh_list(select=("page", name))', body)
        # DELETE takes the selected skill and refuses a header
        self.assertIn('if row and row[0] == "section":', body)
        # Alt+L the list, Alt+O Section…, the initial focus on the list
        self.assertIn('"l": skills_list.tree, "t": text_editor,', body)
        self.assertIn('"o": skills_list.section_btn,', body)
        self.assertIn("skills_list.tree.focus_set()", body)
        # The close lets the list object go after the pane
        close = body[body.index("def _on_skills_close():"):body.index("win.protocol(")]
        self.assertLess(close.index("self._skills_list_pane = None"),
                        close.index("self._skills_list = None"))
        self.assertNotIn("tk.Listbox(", body)
        self.assertNotIn("[5:]", body)                     # no prefix stripping: the id is the name

    def test_the_instruction_editor_builds_the_same_widget_and_keeps_no_copy(self):
        src = _read("myagent", "instructions_mixin.py")
        self.assertIn('pane, title="INSTRUCTIONS", noun="instruction",', src)
        self.assertIn("load=self._load_saved_instructions, save=self._save_instructions_to_disk,", src)
        self.assertIn("on_select=self._on_instruction_selected,", src)
        for gone in ("def _ask_section", "def _open_section_dialog", "def _on_tree_press",
                     "def _on_tree_return", "def _move_selected_instruction", "identify_row",
                     "tree.tag_configure(", "list_title_band("):
            self.assertNotIn(gone, src, gone)
        # The pinned delegates stay (tests/test_instruction_list.py drives them)
        for kept in ("def _populate_instruction_tree", "def _row_of", "def _refresh_instruction_list",
                     "def _select_instruction_row", "def _selected_instruction_row",
                     "def _selected_instruction_name", "def _on_section_toggled",
                     "def _section_for_new_page", "def _collapsed"):
            self.assertIn(kept, src, kept)
        self.assertIn("self._instr_list = None", src)        # nullified with the other widgets

    def test_the_app_starts_with_the_skills_list_state(self):
        src = _read("MyAgent.py")
        self.assertIn("self._skills_list = None", src)
        self.assertIn("self._skills_collapsed_sections = set()", src)
        self.assertLess(src.index("self._collapsed_sections = set()"),
                        src.index("self._skills_collapsed_sections = set()"))

    def test_the_field_colour_walk_recolours_every_sectioned_list(self):
        src = _read("myagent", "other_setup_mixin.py")
        start = src.index("def _theme_recolor_fields(")
        walk = src[start:src.index("\n    def ", start + 1)]
        self.assertIn('if cls_name == "Treeview" and str(widget.cget("style")) == INSTR_TREE_STYLE:',
                      walk)
        self.assertNotIn("break", walk)                       # every tree under the root, not the first


if __name__ == "__main__":
    unittest.main()
