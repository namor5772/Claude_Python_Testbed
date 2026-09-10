"""Characterization tests for myagent/instruction_layout.py (2026-09-11): the
sections-and-order model behind the Instruction Editor's Load Instruction
list. Pure dict-in / dict-out — no Tk, no files.

The layout lives on the entries themselves ("section" / "order"), so these
tests also pin what an untouched store looks like (no keys), what a store
written by an older MyAgent / the manage_instructions tool / Heartbeat looks
like (missing keys → unfiled, alphabetical, last), and that every edit
renumbers the whole store into 0..n-1 display order.
"""

import unittest

from myagent import instruction_layout as il


def store(*rows):
    """A store from (name, section, order) rows; section None / order None = absent."""
    out = {}
    for name, section, order in rows:
        entry = {"text": name}
        if section is not None:
            entry["section"] = section
        if order is not None:
            entry["order"] = order
        out[name] = entry
    return out


def shape(instructions):
    return il.layout(instructions)


class LayoutTests(unittest.TestCase):

    def test_untouched_store_is_one_unfiled_section_in_name_order(self):
        s = store(("Weather", None, None), ("anz", None, None), ("Banking", None, None))
        self.assertEqual(shape(s), [("", ["anz", "Banking", "Weather"])])
        self.assertEqual(il.sections(s), [])

    def test_sections_in_first_page_order_and_unfiled_last(self):
        s = store(("W1", "Weather", 3), ("B2", "Banking", 1), ("B1", "Banking", 0),
                  ("Loose", None, 2), ("W2", "Weather", 4))
        self.assertEqual(shape(s), [("Banking", ["B1", "B2"]), ("Weather", ["W1", "W2"]),
                                    ("", ["Loose"])])
        self.assertEqual(il.sections(s), ["Banking", "Weather"])
        self.assertEqual(il.rows(s), [("Banking", "B1"), ("Banking", "B2"),
                                      ("Weather", "W1"), ("Weather", "W2"), ("", "Loose")])

    def test_pages_without_an_order_go_last_in_their_section_alphabetically(self):
        # what a page created by the manage_instructions tool on another
        # machine, or a newer entry with a section but no number, looks like
        s = store(("Z", "Banking", 0), ("b", "Banking", None), ("A", "Banking", None),
                  ("bad", "Banking", "7"), ("flag", "Banking", True))
        self.assertEqual(shape(s), [("Banking", ["Z", "A", "b", "bad", "flag"])])

    def test_section_values_are_normalized(self):
        s = store(("a", "  Bank   ing ", 0), ("b", "unfiled", 1), ("c", "", 2), ("d", 7, 3))
        self.assertEqual(shape(s), [("Bank ing", ["a"]), ("", ["b", "c", "d"])])
        self.assertEqual(il.normalize_section(" UNFILED "), il.UNFILED)
        self.assertEqual(il.normalize_section(None), il.UNFILED)

    def test_renumber_writes_display_order_and_drops_the_unfiled_key(self):
        s = store(("a", "S", 5), ("b", None, None))
        il.renumber(s, [("", "b"), ("S", "a")])
        self.assertEqual(s["b"], {"text": "b", "order": 0})
        self.assertEqual(s["a"], {"text": "a", "order": 1, "section": "S"})
        self.assertNotIn("section", s["b"])


class MovePageTests(unittest.TestCase):

    def setUp(self):
        self.s = store(("B1", "Banking", 0), ("B2", "Banking", 1),
                       ("W1", "Weather", 2), ("Loose", None, 3))

    def test_swap_within_a_section(self):
        self.assertTrue(il.move_page(self.s, "B2", -1))
        self.assertEqual(shape(self.s), [("Banking", ["B2", "B1"]), ("Weather", ["W1"]),
                                         ("", ["Loose"])])
        # every page renumbered in display order
        self.assertEqual([self.s[n]["order"] for n in ("B2", "B1", "W1", "Loose")], [0, 1, 2, 3])

    def test_stepping_past_the_section_edge_joins_the_neighbour(self):
        self.assertTrue(il.move_page(self.s, "B2", 1))    # down out of Banking
        self.assertEqual(shape(self.s), [("Banking", ["B1"]), ("Weather", ["B2", "W1"]),
                                         ("", ["Loose"])])
        self.assertTrue(il.move_page(self.s, "Loose", -1))  # up out of Unfiled
        self.assertEqual(shape(self.s), [("Banking", ["B1"]), ("Weather", ["B2", "W1", "Loose"])])
        self.assertEqual(self.s["Loose"]["section"], "Weather")

    def test_walking_the_only_page_out_dissolves_its_section(self):
        self.assertTrue(il.move_page(self.s, "W1", 1))
        self.assertEqual(shape(self.s), [("Banking", ["B1", "B2"]), ("", ["W1", "Loose"])])
        self.assertNotIn("section", self.s["W1"])

    def test_ends_and_unknowns_are_no_ops(self):
        before = shape(self.s)
        self.assertFalse(il.move_page(self.s, "B1", -1))
        self.assertFalse(il.move_page(self.s, "Loose", 1))
        self.assertFalse(il.move_page(self.s, "Ghost", 1))
        self.assertFalse(il.move_page(self.s, "B1", 2))
        self.assertEqual(shape(self.s), before)

    def test_first_move_on_an_untouched_store_freezes_the_alphabetical_order(self):
        s = store(("c", None, None), ("a", None, None), ("b", None, None))
        self.assertTrue(il.move_page(s, "c", -1))
        self.assertEqual(shape(s), [("", ["a", "c", "b"])])
        self.assertEqual({n: s[n]["order"] for n in s}, {"a": 0, "c": 1, "b": 2})


class MoveSectionTests(unittest.TestCase):

    def setUp(self):
        self.s = store(("B1", "Banking", 0), ("W1", "Weather", 1), ("W2", "Weather", 2),
                       ("C1", "Code", 3), ("Loose", None, 4))

    def test_swap_with_the_neighbouring_section(self):
        self.assertTrue(il.move_section(self.s, "Code", -1))
        self.assertEqual(shape(self.s), [("Banking", ["B1"]), ("Code", ["C1"]),
                                         ("Weather", ["W1", "W2"]), ("", ["Loose"])])
        self.assertEqual([self.s[n]["order"] for n in ("B1", "C1", "W1", "W2", "Loose")],
                         [0, 1, 2, 3, 4])

    def test_unfiled_stays_last_and_cannot_move(self):
        self.assertFalse(il.move_section(self.s, "Code", 1))
        self.assertFalse(il.move_section(self.s, "", -1))
        self.assertFalse(il.move_section(self.s, "Banking", -1))
        self.assertFalse(il.move_section(self.s, "Ghost", 1))


class FilePageTests(unittest.TestCase):

    def setUp(self):
        self.s = store(("B1", "Banking", 0), ("W1", "Weather", 1), ("Loose", None, 2))

    def test_into_an_existing_section_goes_last_there(self):
        self.assertTrue(il.file_page(self.s, "Loose", "Banking"))
        self.assertEqual(shape(self.s), [("Banking", ["B1", "Loose"]), ("Weather", ["W1"])])

    def test_a_new_name_creates_a_section_after_the_last_one(self):
        self.assertTrue(il.file_page(self.s, "B1", " Mail "))
        self.assertEqual(shape(self.s), [("Weather", ["W1"]), ("Mail", ["B1"]), ("", ["Loose"])])

    def test_the_first_ever_section_goes_before_the_unfiled_block(self):
        s = store(("a", None, None), ("b", None, None))
        self.assertTrue(il.file_page(s, "b", "New"))
        self.assertEqual(shape(s), [("New", ["b"]), ("", ["a"])])

    def test_unfiling_goes_to_the_very_end(self):
        for label in ("", "Unfiled", "  unfiled "):
            s = store(("B1", "Banking", 0), ("W1", "Weather", 1), ("Loose", None, 2))
            self.assertTrue(il.file_page(s, "B1", label), label)
            self.assertEqual(shape(s), [("Weather", ["W1"]), ("", ["Loose", "B1"])])
            self.assertNotIn("section", s["B1"])

    def test_already_there_and_unknown_are_no_ops(self):
        self.assertFalse(il.file_page(self.s, "B1", "Banking"))
        self.assertFalse(il.file_page(self.s, "Loose", "Unfiled"))
        self.assertFalse(il.file_page(self.s, "Ghost", "Banking"))


class RenameSectionTests(unittest.TestCase):

    def setUp(self):
        self.s = store(("B1", "Banking", 0), ("B2", "Banking", 1), ("W1", "Weather", 2),
                       ("Loose", None, 3))

    def test_rename_keeps_the_position(self):
        self.assertTrue(il.rename_section(self.s, "Banking", "Money"))
        self.assertEqual(shape(self.s), [("Money", ["B1", "B2"]), ("Weather", ["W1"]),
                                         ("", ["Loose"])])

    def test_rename_onto_an_existing_section_merges_after_its_pages(self):
        self.assertTrue(il.rename_section(self.s, "Banking", "Weather"))
        self.assertEqual(shape(self.s), [("Weather", ["W1", "B1", "B2"]), ("", ["Loose"])])
        self.assertEqual([self.s[n]["order"] for n in ("W1", "B1", "B2", "Loose")], [0, 1, 2, 3])

    def test_rename_to_unfiled_unfiles_every_page(self):
        self.assertTrue(il.rename_section(self.s, "Banking", "Unfiled"))
        self.assertEqual(shape(self.s), [("Weather", ["W1"]), ("", ["Loose", "B1", "B2"])])
        s = store(("B1", "Banking", 0))   # ...even when there was no unfiled block yet
        self.assertTrue(il.rename_section(s, "Banking", ""))
        self.assertEqual(shape(s), [("", ["B1"])])

    def test_no_ops(self):
        self.assertFalse(il.rename_section(self.s, "Banking", "Banking"))
        self.assertFalse(il.rename_section(self.s, "", "Anything"))
        self.assertFalse(il.rename_section(self.s, "Ghost", "X"))


class DropTests(unittest.TestCase):

    def setUp(self):
        self.s = store(("B1", "Banking", 0), ("B2", "Banking", 1), ("B3", "Banking", 2),
                       ("W1", "Weather", 3), ("Loose", None, 4))

    def test_page_dragged_down_lands_after_the_target(self):
        self.assertTrue(il.drop(self.s, "page", "B1", "page", "B3"))
        self.assertEqual(shape(self.s)[0], ("Banking", ["B2", "B3", "B1"]))

    def test_page_dragged_up_lands_before_the_target(self):
        self.assertTrue(il.drop(self.s, "page", "B3", "page", "B1"))
        self.assertEqual(shape(self.s)[0], ("Banking", ["B3", "B1", "B2"]))

    def test_page_dropped_on_a_page_of_another_section_changes_section(self):
        self.assertTrue(il.drop(self.s, "page", "Loose", "page", "B2"))
        self.assertEqual(shape(self.s), [("Banking", ["B1", "Loose", "B2", "B3"]),
                                         ("Weather", ["W1"])])
        self.assertTrue(il.drop(self.s, "page", "B1", "page", "W1"))   # down, into Weather
        self.assertEqual(shape(self.s), [("Banking", ["Loose", "B2", "B3"]),
                                         ("Weather", ["W1", "B1"])])

    def test_page_dropped_on_a_header_becomes_that_sections_first_page(self):
        self.assertTrue(il.drop(self.s, "page", "W1", "section", "Banking"))
        self.assertEqual(shape(self.s), [("Banking", ["W1", "B1", "B2", "B3"]), ("", ["Loose"])])
        self.assertTrue(il.drop(self.s, "page", "B3", "section", ""))   # onto Unfiled
        self.assertEqual(shape(self.s), [("Banking", ["W1", "B1", "B2"]), ("", ["B3", "Loose"])])
        # a header that no longer exists (Weather emptied above) is no target
        self.assertFalse(il.drop(self.s, "page", "W1", "section", "Weather"))
        # ...nor is a section's own header when the dragged page is its only one
        s = store(("Only", "Solo", 0), ("Other", None, 1))
        self.assertFalse(il.drop(s, "page", "Only", "section", "Solo"))

    def test_section_dropped_on_a_row_takes_that_sections_place(self):
        self.assertTrue(il.drop(self.s, "section", "Weather", "page", "B2"))   # dragged up
        self.assertEqual([sec for sec, _ in shape(self.s)], ["Weather", "Banking", ""])
        self.assertTrue(il.drop(self.s, "section", "Weather", "section", "Banking"))  # down
        self.assertEqual([sec for sec, _ in shape(self.s)], ["Banking", "Weather", ""])
        self.assertTrue(il.drop(self.s, "section", "Banking", "section", ""))  # onto Unfiled
        self.assertEqual([sec for sec, _ in shape(self.s)], ["Weather", "Banking", ""])

    def test_no_ops(self):
        before = shape(self.s)
        self.assertFalse(il.drop(self.s, "page", "B1", "page", "B1"))
        self.assertFalse(il.drop(self.s, "page", "Ghost", "page", "B1"))
        self.assertFalse(il.drop(self.s, "page", "B1", "page", "Ghost"))
        self.assertFalse(il.drop(self.s, "page", "B1", "section", "Ghost"))
        self.assertFalse(il.drop(self.s, "section", "", "section", "Banking"))
        self.assertFalse(il.drop(self.s, "section", "Banking", "page", "B2"))
        self.assertFalse(il.drop(self.s, "section", "Ghost", "page", "B2"))
        self.assertFalse(il.drop(self.s, "row", "B1", "page", "B2"))
        self.assertEqual(shape(self.s), before)


if __name__ == "__main__":
    unittest.main()
