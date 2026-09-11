"""Characterization tests for the Instruction Editor's Instructions LIST
(instructions_mixin.py, 2026-09-11) — the widget half of the sections-and-pages
model whose pure half is tests/test_instruction_layout.py.

Builds a real ttk.Treeview on a withdrawn Tk root (like the toolbar-highlight
tests; skips where there is no display) and drives the mixin's list helpers on
a bare stub, so the row-id scheme, the header / zebra tags, the collapsed-
section memory and the selection helpers are pinned without the App.
"""

import tkinter as tk
from tkinter import ttk
import unittest

from tests._util import stub
from myagent.instructions_mixin import InstructionsMixin

STORE = {
    "B2": {"text": "b2", "section": "Banking", "order": 1},
    "B1": {"text": "b1", "section": "Banking", "order": 0},
    "W1": {"text": "w1", "section": "Weather", "order": 2},
    "Loose": {"text": "loose"},
}


class _TreeCase(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        self.tree = ttk.Treeview(self.root, show="tree headings")
        self.tree.pack()
        self.host = stub(InstructionsMixin, _instr_tree=self.tree)

    def tearDown(self):
        self.root.destroy()

    def rows(self):
        out = []
        for header in self.tree.get_children(""):
            out.append((header, self.tree.item(header, "text"), self.tree.item(header, "open"),
                        tuple(self.tree.item(header, "tags")),
                        [(p, tuple(self.tree.item(p, "tags"))) for p in self.tree.get_children(header)]))
        return out


class PopulateTests(_TreeCase):

    def test_headers_pages_ids_and_tags(self):
        InstructionsMixin._populate_instruction_tree(self.tree, STORE)
        self.assertEqual(self.rows(), [
            ("s:Banking", "Banking", True, ("section",), [("p:B1", ()), ("p:B2", ("odd",))]),
            ("s:Weather", "Weather", True, ("section",), [("p:W1", ())]),
            ("s:", "Unfiled", True, ("unfiled",), [("p:Loose", ())]),
        ])

    def test_collapsed_sections_start_closed_and_a_rebuild_replaces_everything(self):
        InstructionsMixin._populate_instruction_tree(self.tree, STORE, collapsed={"Weather"})
        opens = {h: self.tree.item(h, "open") for h in self.tree.get_children("")}
        self.assertEqual(opens, {"s:Banking": True, "s:Weather": False, "s:": True})
        InstructionsMixin._populate_instruction_tree(self.tree, {"Only": {"text": "x"}})
        self.assertEqual([h for h, *_ in self.rows()], ["s:"])
        self.assertEqual(self.tree.get_children("s:"), ("p:Only",))

    def test_row_of(self):
        self.assertEqual(InstructionsMixin._row_of("p:Weather Agent"), ("page", "Weather Agent"))
        self.assertEqual(InstructionsMixin._row_of("s:Banking"), ("section", "Banking"))
        self.assertEqual(InstructionsMixin._row_of("s:"), ("section", ""))


class SelectionTests(_TreeCase):

    def setUp(self):
        super().setUp()
        InstructionsMixin._populate_instruction_tree(self.tree, STORE, collapsed={"Weather"})
        self.host._collapsed_sections = {"Weather"}

    def test_nothing_selected(self):
        self.assertIsNone(self.host._selected_instruction_row())
        self.assertEqual(self.host._selected_instruction_name(), "")

    def test_select_a_page_a_header_and_clear(self):
        self.host._select_instruction_row(("page", "B2"))
        self.assertEqual(self.tree.selection(), ("p:B2",))
        self.assertEqual(self.host._selected_instruction_row(), ("page", "B2"))
        self.assertEqual(self.host._selected_instruction_name(), "B2")
        self.host._select_instruction_row(("section", ""))
        self.assertEqual(self.host._selected_instruction_row(), ("section", ""))
        self.assertEqual(self.host._selected_instruction_name(), "")   # a header is no page
        self.host._select_instruction_row(None)
        self.assertEqual(self.tree.selection(), ())
        self.host._select_instruction_row(("page", "Ghost"))   # unknown → cleared
        self.assertEqual(self.tree.selection(), ())

    def test_selecting_a_page_in_a_collapsed_section_opens_it_for_real(self):
        # `see` opens the parent; the remembered set must say so too, or the
        # next rebuild would fold the section back over its own selection
        self.host._select_instruction_row(("page", "W1"))
        self.assertTrue(self.tree.item("s:Weather", "open"))
        self.assertEqual(self.host._collapsed_sections, set())

    def test_refresh_keeps_the_selection_or_takes_the_requested_one(self):
        store = dict(STORE)
        self.host._load_saved_instructions = lambda: store
        self.host._select_instruction_row(("page", "B1"))
        self.host._refresh_instruction_list()
        self.assertEqual(self.tree.selection(), ("p:B1",))
        self.host._refresh_instruction_list(store, select=("section", "Banking"))
        self.assertEqual(self.tree.selection(), ("s:Banking",))
        del store["B1"]
        self.host._select_instruction_row(("page", "B1"))
        self.host._refresh_instruction_list(store)   # the selected row is gone
        self.assertEqual(self.tree.selection(), ())
        self.assertNotIn("p:B1", self.tree.get_children("s:Banking"))

    def test_the_toggle_events_maintain_the_collapsed_set(self):
        self.tree.focus("s:Banking")
        self.host._on_section_toggled(opened=False)
        self.assertEqual(self.host._collapsed_sections, {"Weather", "Banking"})
        self.host._on_section_toggled(opened=True)
        self.assertEqual(self.host._collapsed_sections, {"Weather"})
        self.tree.focus("p:B1")   # a page has no open state to remember
        self.host._on_section_toggled(opened=False)
        self.assertEqual(self.host._collapsed_sections, {"Weather"})

    def test_a_new_page_is_filed_where_the_selection_is(self):
        self.assertEqual(self.host._section_for_new_page(STORE), "")
        self.host._select_instruction_row(("page", "B2"))
        self.assertEqual(self.host._section_for_new_page(STORE), "Banking")
        self.host._select_instruction_row(("section", "Weather"))
        self.assertEqual(self.host._section_for_new_page(STORE), "Weather")
        self.host._select_instruction_row(("section", ""))
        self.assertEqual(self.host._section_for_new_page(STORE), "")


class BuildTests(unittest.TestCase):
    """The real builder (_build_instruction_list) on a withdrawn root: what
    the user reads over the list."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_the_list_is_headed_instructions(self):
        # "Instructions" since 2026-09-12: the first day's "Load Instruction"
        # (the old combobox's label) read like a button to press, when
        # selecting a page is the load.
        host = stub(InstructionsMixin)
        host._build_instruction_list(tk.Frame(self.root))
        self.assertEqual(host._instr_tree.heading("#0", "text"), "Instructions")


if __name__ == "__main__":
    unittest.main()
