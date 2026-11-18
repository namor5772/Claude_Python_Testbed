"""A place for a sheet, and excel_zoom (2026-11-18) — ExcelMixin's other two layout pieces.

`excel_sheet` gained 'before' / 'after' for add and a move action; `excel_zoom`
is new. No Excel and no xlwings here: the tools run over the fakes of
tests/test_excel_tables_panes.py, which BEHAVE as a real Excel was measured to
that day. For these two that means:

  * Worksheet.Move without a real sheet as its first argument does not fail —
    the sheet LEAVES the workbook for a new one. Passing the target as After
    is one way to get there;
  * in a workbook that is not in front, Excel allows for the removal of that
    workbook's ACTIVE sheet instead of the moved one, so the sheet lands one
    tab off whenever the two lie on opposite sides of the destination
    (MeasuredMoveTests holds what it did with every such move of four sheets);
  * Move shows the moved sheet and brings its workbook forward;
  * xlwings makes a new sheet first and names it second, so a name Excel
    refuses leaves a sheet behind;
  * xlwings' "add after" counts tabs through Worksheet.Index over a
    collection of worksheets only: with a chart sheet ahead the new sheet
    lands a tab late, and beside the last worksheets ANOTHER sheet is moved;
  * a zoom outside 10-400 is refused with a bare "Unable to set the Zoom
    property of the Window class", and a fraction is dropped without a word.

So a tool that moves in any other form, or forgets to bring the workbook
forward, or to put the view back, or trusts xlwings' "after" behind a chart
sheet, comes out with a wrong ORDER or a missing sheet — not merely a
different call log. On a real Excel: sections 12 and 13 of
tests/check_excel_live.py.
"""

import unittest

import myagent.constants as c
from myagent.excel_mixin import ExcelMixin
from tests.test_excel_tables_panes import OPAQUE, _Book, _Chart, _Host, _PlatformCase


# ── pure helpers ─────────────────────────────────────────────────────────────

class SheetNameRuleTests(unittest.TestCase):
    """_excel_sheet_name_problem against what Excel itself answered on
    2026-11-18 when each name was given to an existing sheet. True = taken."""

    EXCEL_SAID = {
        "Documents": True, "x" * 31: True, "a'b": True, "Sheet 1": True, "x.y": True, "déjà": True,
        "a,b": True, "a!b": True, "a=b": True, "1": True, "A1": True, "#x": True,
        "x" * 32: False, "a/b": False, "a\\b": False, "a?b": False, "a*b": False, "a[b": False,
        "a]b": False, "a:b": False, "'a": False, "a'": False, "History": False, "history": False,
        "": False,
    }

    def test_the_rule_agrees_with_excel_on_every_measured_name(self):
        for name, taken in self.EXCEL_SAID.items():
            with self.subTest(name=name[:12]):
                self.assertEqual(ExcelMixin._excel_sheet_name_problem(name) is None, taken)

    def test_each_refusal_says_why(self):
        problem = ExcelMixin._excel_sheet_name_problem
        self.assertIn("32 characters long (31 at most)", problem("x" * 32))
        self.assertIn("it contains /", problem("a/b"))
        self.assertIn("it contains : [", problem("a:b[c:"))        # each once, in the order met
        self.assertIn("apostrophe", problem("'a"))
        self.assertIn("reserves", problem("History"))
        self.assertIn("empty", problem(""))


class NumberAndPlaceTests(unittest.TestCase):
    def test_a_zoom_is_a_whole_number_from_10_to_400(self):
        percent = ExcelMixin._excel_zoom_percent
        for value in (85, 85.0, "85", " 85 "):
            self.assertEqual(percent(value), 85)
        self.assertEqual((percent(10), percent(400)), (10, 400))
        for value in (9, 401, -5, 0, 85.5, "abc", "", None, True, [85], float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as ctx:
                    percent(value)
                self.assertIn("'percent'", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            percent(85.6)                       # Excel would take it, and make it 85
        self.assertIn("whole number", str(ctx.exception))

    def test_the_pane_counts_still_parse_through_the_shared_rule(self):
        self.assertEqual(ExcelMixin._excel_pane_count("8", "rows", 1_048_576), 8)
        with self.assertRaises(ValueError) as ctx:
            ExcelMixin._excel_pane_count(2.5, "rows", 1_048_576)
        self.assertEqual(str(ctx.exception), "'rows' must be a whole number (0 = none), got 2.5")

    def test_where_a_sheet_goes(self):
        where = ExcelMixin._excel_sheet_where
        self.assertEqual(where({"before": " Spending "}), ("before", "Spending"))
        self.assertEqual(where({"after": "Spending"}), ("after", "Spending"))
        self.assertEqual(where({}), (None, None))
        self.assertEqual(where({"before": "", "after": None}), (None, None))
        with self.assertRaises(ValueError) as ctx:
            where({"before": "A", "after": "B"})
        self.assertIn("not both", str(ctx.exception))

    def test_in_place_means_directly_beside(self):
        placed = ExcelMixin._excel_sheet_in_place
        names = ["A", "B", "C", "D"]
        self.assertTrue(placed(names, "B", "after", "A"))
        self.assertTrue(placed(names, "B", "before", "C"))
        self.assertFalse(placed(names, "C", "after", "A"))      # after, but not directly
        self.assertFalse(placed(names, "A", "after", "B"))
        self.assertFalse(placed(names, "Z", "after", "A"))      # a sheet that left is not in place
        self.assertFalse(placed(names, "A", "after", "Z"))

    def test_the_order_asked_for_keeps_every_other_sheet_where_it_was(self):
        order = ExcelMixin._excel_sheet_order
        names = ["A", "B", "C", "D"]
        self.assertEqual(order(names, "D", "before", "B"), ["A", "D", "B", "C"])
        self.assertEqual(order(names, "A", "after", "D"), ["B", "C", "D", "A"])
        self.assertEqual(order(names, "B", "after", "A"), names)                        # already there
        self.assertEqual(order(names, "N", "after", "B"), ["A", "B", "N", "C", "D"])    # a sheet new to the list
        self.assertEqual(order(names, "N", "before", "A"), ["N", "A", "B", "C", "D"])
        self.assertEqual(names, ["A", "B", "C", "D"])                                   # the list given is untouched


class SchemaTests(unittest.TestCase):
    @staticmethod
    def schema(name):
        return next(t for t in c.EXCEL_TOOLS if t["name"] == name)["input_schema"]

    def test_move_is_an_action_of_the_one_tuple(self):
        sheet = self.schema("excel_sheet")
        self.assertIn("move", c.EXCEL_SHEET_ACTIONS)
        self.assertEqual(sheet["properties"]["action"]["enum"], list(c.EXCEL_SHEET_ACTIONS))
        self.assertIs(ExcelMixin.SHEET_ACTIONS, c.EXCEL_SHEET_ACTIONS)
        self.assertEqual(sheet["required"], ["action"])
        for key in ("before", "after"):
            self.assertEqual(sheet["properties"][key]["type"], "string")

    def test_zoom_takes_one_optional_number(self):
        zoom = self.schema("excel_zoom")
        self.assertEqual(zoom["required"], [])
        self.assertEqual({k: v["type"] for k, v in zoom["properties"].items()},
                         {"workbook": "string", "sheet": "string", "percent": "number"})
        self.assertTrue(callable(getattr(ExcelMixin, "do_excel_zoom", None)))
        self.assertNotIn("excel_zoom", c.PARALLEL_SAFE_TOOLS)


# ── excel_sheet: add at a place, move ────────────────────────────────────────

SHEETS = ("Cover", "Spending", "Documents", "Data Sources")


class _SheetCase(_PlatformCase):
    def setUp(self):
        super().setUp()
        self.book = _Book("books.xlsx", sheets=SHEETS)
        self.other = _Book("other.xlsx", app=self.book.app)
        self.book.app.books.active = self.other          # the user's workbook is in front
        self.h = _Host(self.book)

    def sheet(self, action, **params):
        return self.h.do_excel_sheet(dict(params, action=action))

    def front(self):
        return self.book.app.books.active.name

    def added(self):
        return [e for e in self.book.log if e[0] == "sheets.add"]


class SheetAddTests(_SheetCase):
    def test_without_a_place_the_new_sheet_goes_last_as_it_always_did(self):
        out = self.sheet("add", name="Notes")
        self.assertEqual(out, "Added sheet 'Notes' to books.xlsx. Sheets: Cover, Spending, Documents, "
                              "Data Sources, Notes")
        self.assertEqual(self.added(), [("sheets.add", "Notes", None, "Data Sources")])

    def test_after_a_sheet(self):
        out = self.sheet("add", name="Receipts", after="spending")      # the real name, whatever the case
        self.assertEqual(out, "Added sheet 'Receipts' to books.xlsx after 'Spending'. Sheets: Cover, "
                              "Spending, Receipts, Documents, Data Sources")
        self.assertEqual(self.book.moves(), [])                         # xlwings placed it: no move needed

    def test_before_a_sheet(self):
        out = self.sheet("add", name="Index", before="Cover")
        self.assertTrue(out.startswith("Added sheet 'Index' to books.xlsx before 'Cover'. Sheets: Index, Cover"))

    def test_the_new_sheet_is_in_view_in_its_workbook_and_the_front_workbook_stays(self):
        # xlwings' after-the-last route ends in an Activate that brings the
        # workbook forward; every other route does not. Now none does.
        for params in ({}, {"after": "Data Sources"}, {"after": "Cover"}, {"before": "Cover"}):
            with self.subTest(params=params):
                book = _Book("books.xlsx", sheets=SHEETS)
                other = _Book("other.xlsx", app=book.app)
                book.app.books.active = other
                _Host(book).do_excel_sheet(dict(params, action="add", name="New"))
                self.assertEqual(book.sheets.active.name, "New")
                self.assertIs(book.app.books.active, other)

    def test_a_name_excel_would_refuse_is_refused_before_a_sheet_exists(self):
        # xlwings adds first and names second: 'a/b' used to leave a Sheet5.
        for name, why in (("a/b", "it contains /"), ("x" * 32, "31 at most"), ("History", "reserves"),
                          ("'tis", "apostrophe")):
            with self.subTest(name=name[:8]):
                out = self.sheet("add", name=name, after="Cover")
                self.assertIn("cannot be a sheet name", out)
                self.assertIn(why, out)
                self.assertIn("Nothing was added.", out)
        self.assertEqual(self.added(), [])
        self.assertEqual(self.book.order(), list(SHEETS))

    def test_a_name_in_use_is_refused_with_the_sheets_there_are(self):
        out = self.sheet("add", name="documents")
        self.assertEqual(out, "excel_sheet error: books.xlsx already has a sheet named 'Documents'. "
                              "Nothing was added. Sheets: Cover, Spending, Documents, Data Sources")
        self.assertEqual(self.added(), [])

    def test_two_places_or_an_unknown_one_are_refused(self):
        self.assertEqual(self.sheet("add", name="Q", before="Cover", after="Spending"),
                         "excel_sheet error: give 'before' or 'after', not both")
        out = self.sheet("add", name="Q", after="Nope")
        self.assertIn("Sheet 'Nope' not found in books.xlsx. Sheets: Cover, Spending", out)
        self.assertEqual(self.added(), [])

    def test_a_protected_structure_is_said_before_excel_refuses(self):
        self.book.protected = True
        out = self.sheet("add", name="Q", after="Cover")
        self.assertIn("the structure of books.xlsx is protected, so no sheet can be added", out)
        self.assertEqual(self.added(), [])

    def test_the_fake_adds_after_as_xlwings_was_measured_to_with_a_chart_sheet_ahead(self):
        # A real Excel, 2026-11-18: sheets.add('RAW', after=…) in Chart1, A, B, C, D.
        measured = {"A": "Chart1 A B RAW C D", "B": "Chart1 A B C RAW D",       # a tab late
                    "C": "Chart1 A B RAW D C",                                  # ... and C and D swapped
                    "D": "Chart1 A B C D RAW"}
        for ref, left in measured.items():
            with self.subTest(after=ref):
                book = _Book("m.xlsx", sheets="ABCD")
                book.tabs.insert(0, _Chart(book))
                book.sheets.add("RAW", after=book.sheets[ref])
                self.assertEqual(" ".join(book.order()), left)
        book = _Book("m.xlsx", sheets="ABCD")
        book.tabs[0:0] = [_Chart(book, "Chart1"), _Chart(book, "Chart2")]
        book.sheets.add("RAW", after=book.sheets["B"])
        self.assertEqual(" ".join(book.order()), "Chart1 Chart2 A RAW B D C")

    def test_a_chart_sheet_ahead_is_what_puts_the_index_off(self):
        off = ExcelMixin._excel_sheet_index_off
        self.assertFalse(off(self.book, self.book.sheets["Documents"]))
        self.book.tabs.insert(2, _Chart(self.book))                     # Cover, Spending, Chart1, Documents, …
        self.assertFalse(off(self.book, self.book.sheets["Spending"]))  # the chart sheet is after this one
        self.assertTrue(off(self.book, self.book.sheets["Documents"]))

    def test_with_chart_sheets_ahead_every_place_is_right_and_no_other_sheet_moves(self):
        for charts in (1, 2):
            for how in ("before", "after"):
                for ref in "ABCD":
                    with self.subTest(charts=charts, how=how, ref=ref):
                        book = _Book("m.xlsx", sheets="ABCD")
                        other = _Book("other.xlsx", app=book.app)
                        book.app.books.active = other
                        book.tabs[0:0] = [_Chart(book, f"Chart{n + 1}") for n in range(charts)]
                        out = _Host(book).do_excel_sheet({"action": "add", "name": "N", how: ref})
                        want = list("ABCD")
                        want.insert(want.index(ref) + (how == "after"), "N")
                        self.assertEqual([s.name for s in book.sheets], want, out)
                        self.assertNotIn("⚠", out)
                        self.assertEqual(book.sheets.active.name, "N")
                        self.assertIs(book.app.books.active, other)

    def test_xlwings_own_after_is_not_used_where_a_chart_sheet_lies_ahead(self):
        self.book.tabs.insert(0, _Chart(self.book))
        out = self.sheet("add", name="Receipts", after="Documents")
        self.assertIn("after 'Documents'. Sheets: Cover, Spending, Documents, Receipts, Data Sources", out)
        self.assertEqual(self.added(), [("sheets.add", "Receipts", "Documents", None)])     # added BEFORE it
        self.assertEqual(self.book.moves(), [("Receipts", "Data Sources", None)])           # ... and moved after
        self.assertEqual(self.front(), "other.xlsx")

    def test_other_sheets_that_moved_are_never_passed_off_as_a_clean_add(self):
        # The tool as it was first written — xlwings' "after" whatever lies
        # ahead — left the new sheet beside the one named with two OTHER
        # sheets swapped. Beside the right sheet is not the order asked for.
        class Trusting(_Host):
            @staticmethod
            def _excel_sheet_index_off(book, ref):
                return False

        self.book.tabs.insert(0, _Chart(self.book))
        out = Trusting(self.book).do_excel_sheet({"action": "add", "name": "Receipts", "after": "Documents"})
        self.assertIn("Sheets: Cover, Spending, Data Sources, Documents, Receipts", out)
        self.assertIn("⚠ The other sheets are no longer in the order they were", out)

    def test_a_sheet_xlwings_put_in_the_wrong_place_is_moved_to_the_right_one(self):
        # A placement that missed for a reason not yet seen: the order is read back.
        self.book.add_misplaces = True
        out = self.sheet("add", name="Receipts", after="Spending")
        self.assertIn("after 'Spending'. Sheets: Cover, Spending, Receipts, Documents, Data Sources", out)
        self.assertNotIn("⚠", out)
        self.assertEqual(self.book.moves(), [("Receipts", "Documents", None)])
        self.assertEqual(self.book.sheets.active.name, "Receipts")
        self.assertEqual(self.front(), "other.xlsx")

    def test_a_place_that_could_not_be_had_is_not_claimed(self):
        self.book.add_misplaces = self.book.move_stuck = True
        out = self.sheet("add", name="Receipts", after="Spending")
        self.assertIn("Sheets: Cover, Spending, Documents, Data Sources, Receipts", out)
        self.assertIn("⚠ It is not after 'Spending'", out)
        self.assertIn("action=move", out)


class SheetMoveTests(_SheetCase):
    def test_before_a_sheet(self):
        out = self.sheet("move", name="Data Sources", before="spending")
        self.assertEqual(out, "Moved sheet 'Data Sources' before 'Spending' in books.xlsx. Sheets: Cover, "
                              "Data Sources, Spending, Documents")
        self.assertEqual(self.book.moves(), [("Data Sources", "Spending", None)])

    def test_after_a_sheet_is_before_its_follower(self):
        out = self.sheet("move", name="Data Sources", after="Cover")
        self.assertIn("Sheets: Cover, Data Sources, Spending, Documents", out)
        self.assertEqual(self.book.moves(), [("Data Sources", "Spending", None)])

    def test_after_the_last_sheet_is_two_moves_and_never_an_after(self):
        out = self.sheet("move", name="Cover", after="Data Sources")
        self.assertIn("Sheets: Spending, Documents, Data Sources, Cover", out)
        self.assertEqual(self.book.moves(), [("Cover", "Data Sources", None), ("Data Sources", "Cover", None)])

    def test_every_native_move_names_a_real_sheet_first_and_nothing_else(self):
        # The one form that cannot send a sheet out of its workbook.
        for name, params in (("Data Sources", {"before": "Cover"}), ("Cover", {"after": "Data Sources"}),
                             ("Spending", {"after": "Documents"}), ("Documents", {"before": "Spending"}),
                             ("Cover", {"after": "Spending"})):
            with self.subTest(name=name, params=params):
                book = _Book("books.xlsx", sheets=SHEETS)
                out = _Host(book).do_excel_sheet(dict(params, action="move", name=name))
                self.assertTrue(out.startswith("Moved sheet"), out)
                self.assertTrue(book.moves())
                for _moved, before, after in book.moves():
                    self.assertIsNotNone(before)
                    self.assertIsNone(after)
                self.assertEqual(book.exiled, [])
                self.assertEqual(sorted(book.order()), sorted(SHEETS))

    def test_the_workbook_is_brought_forward_first(self):
        # Behind, Excel allows for the wrong sheet leaving (MeasuredMoveTests):
        # with Cover showing, a sheet from the far end asked into the slot
        # right after Cover lands before Cover.
        self.assertIs(self.book.sheets.active, self.book.sheets["Cover"])
        out = self.sheet("move", name="Data Sources", after="Cover")
        self.assertIn("Sheets: Cover, Data Sources, Spending, Documents", out)
        events = [e[0] for e in self.book.log]
        self.assertLess(events.index("book.activate"), events.index("Move"))

    def test_the_fake_does_misplace_a_move_made_behind(self):
        # What the previous test guards against, shown on its own.
        self.book.sheets["Data Sources"].api.Move(self.book.sheets["Spending"].api)
        self.assertEqual(self.book.order(), ["Data Sources", "Cover", "Spending", "Documents"])

    def test_the_view_is_put_back(self):
        self.book.sheets.active = self.book.sheets["Documents"]
        self.sheet("move", name="Data Sources", before="Cover")
        self.assertEqual(self.book.sheets.active.name, "Documents")     # Move had shown the moved sheet
        self.assertEqual(self.front(), "other.xlsx")                    # and brought its workbook forward
        self.assertIn(("book.activate", "other.xlsx"), self.other.log)

    def test_a_workbook_that_was_not_in_front_at_the_first_try_gets_a_second(self):
        self.book.front_stolen = 1          # another workbook came forward between the two calls
        out = self.sheet("move", name="Data Sources", after="Cover")
        self.assertTrue(out.startswith("Moved sheet 'Data Sources' after 'Cover'"))
        self.assertIn("Sheets: Cover, Data Sources, Spending, Documents", out)
        self.assertEqual(len(self.book.moves()), 2)

    def test_already_there_is_said_and_nothing_is_moved(self):
        out = self.sheet("move", name="Spending", after="Cover")
        self.assertEqual(out, "Sheet 'Spending' is already after 'Cover' in books.xlsx — nothing to do. "
                              "Sheets: Cover, Spending, Documents, Data Sources")
        self.assertEqual(self.book.moves(), [])
        self.assertEqual(self.book.log, [])

    def test_what_cannot_be_a_move(self):
        self.assertEqual(self.sheet("move", name="Cover", after="cover"),
                         "excel_sheet error: sheet 'Cover' cannot be moved after itself.")
        self.assertIn("'before' or 'after' is required for move", self.sheet("move", name="Cover"))
        self.assertIn("not both", self.sheet("move", name="Cover", before="Spending", after="Documents"))
        self.assertIn("Sheet 'Nope' not found", self.sheet("move", name="Nope", after="Cover"))
        self.assertIn("Sheet 'Nope' not found", self.sheet("move", name="Cover", after="Nope"))
        self.assertIn("'name' is required for 'move'", self.sheet("move", after="Cover"))
        self.assertEqual(self.book.moves(), [])

    def test_a_protected_structure_is_said_before_excel_refuses_without_a_word(self):
        self.book.protected = True
        out = self.sheet("move", name="Data Sources", before="Cover")
        self.assertIn("the structure of books.xlsx is protected, so no sheet can be moved", out)
        self.assertEqual(self.book.moves(), [])

    def test_a_chart_sheet_next_door_does_not_throw_the_place(self):
        # Worksheet.Index counts chart sheets; xlwings' book.sheets does not list them.
        self.book.tabs.insert(1, _Chart(self.book))             # Cover, Chart1, Spending, …
        out = self.sheet("move", name="Data Sources", after="Cover")
        self.assertIn("Sheets: Cover, Data Sources, Spending, Documents", out)
        self.assertEqual(self.book.order(), ["Cover", "Data Sources", "Chart1", "Spending", "Documents"])
        self.assertEqual(self.book.moves(), [("Data Sources", "Chart1", None)])

    def test_a_sheet_that_did_not_get_there_is_not_claimed_to_have(self):
        self.book.move_stuck = True
        out = self.sheet("move", name="Data Sources", before="Cover")
        self.assertEqual(out, "excel_sheet error: sheet 'Data Sources' did not end up before 'Cover' in "
                              "books.xlsx. Sheets now: Cover, Spending, Documents, Data Sources")
        self.assertEqual(len(self.book.moves()), 2)             # tried, read back, tried once more

    def test_a_move_that_disturbed_another_sheet_is_not_claimed(self):
        # Beside the sheet named is not enough: every other sheet has to be
        # where it was. No Excel has been seen to do this with a move; it is
        # what xlwings' "add after" did, and the read-back is one and the same.
        class Clumsy(_Host):
            @staticmethod
            def _excel_sheet_native_move(book, sht, how, ref):
                tabs = book.tabs
                tabs.remove(sht)
                tabs.insert(tabs.index(ref), sht)
                first, third = tabs.index(book.sheets["Cover"]), tabs.index(book.sheets["Documents"])
                if first < third:
                    tabs[first], tabs[third] = tabs[third], tabs[first]
                book.sync()

        out = Clumsy(self.book).do_excel_sheet({"action": "move", "name": "Data Sources", "before": "Spending"})
        self.assertEqual(out, "excel_sheet error: sheet 'Data Sources' is before 'Spending' in books.xlsx, "
                              "but the other sheets are no longer in the order they were. Sheets now: "
                              "Documents, Data Sources, Spending, Cover")

    def test_a_sheet_that_left_the_workbook_is_said_in_so_many_words(self):
        # What a move without a target does. The tool never makes one; if a
        # change ever lets it, this is what the model must be told.
        class Careless(_Host):
            @staticmethod
            def _excel_sheet_native_move(book, sht, how, ref):
                sht.api.Move()

        out = Careless(self.book).do_excel_sheet({"action": "move", "name": "Data Sources", "before": "Cover"})
        self.assertIn("sheet 'Data Sources' is no longer in books.xlsx", out)
        self.assertIn("into a new workbook", out)
        self.assertIn("Sheets left: Cover, Spending, Documents", out)
        self.assertEqual(self.book.exiled, ["Data Sources"])
        self.assertEqual(len(self.book.moves()), 1)             # no second try over a sheet that is gone

    def test_the_fake_does_send_a_sheet_out_for_an_after(self):
        api = self.book.sheets["Cover"].api
        api.Move(after=self.book.sheets["Spending"].api)        # named After
        self.assertEqual(self.book.exiled, ["Cover"])
        self.book.sheets["Documents"].api.Move(None, self.book.sheets["Spending"].api)   # After by position
        self.assertEqual(self.book.exiled, ["Cover", "Documents"])

    def test_the_other_actions_are_as_they_were(self):
        self.assertIn("unknown action 'frobnicate'", self.sheet("frobnicate", name="Cover"))
        self.assertTrue(self.sheet("activate", name="documents").startswith("Activated sheet 'documents'"))
        self.assertIn("'name' is required for 'add'", self.sheet("add"))


class MeasuredMoveTests(_PlatformCase):
    """The fake's Move against a real Excel's, and the tool over it.

    `Move(Before=…)` was given to Excel 16.0 on 2026-11-18 for every active
    sheet, moved sheet and target among four sheets A-D — 48 moves — in a
    workbook that was BEHIND another. These fourteen landed one tab off; the
    other thirty-four landed where asked, and so did all 48 with the workbook
    in front. One rule fits all 96: behind, Excel allows for the removal of
    the workbook's ACTIVE sheet instead of the moved one."""

    WRONG_BEHIND = {        # (active, moved, before) → the order Excel left
        ("A", "C", "B"): "CABD", ("A", "D", "B"): "DABC", ("A", "D", "C"): "ADBC",
        ("B", "A", "B"): "BACD", ("B", "D", "C"): "ADBC",
        ("C", "A", "B"): "BACD", ("C", "A", "C"): "BCAD", ("C", "B", "C"): "ACBD",
        ("D", "A", "B"): "BACD", ("D", "A", "C"): "BCAD", ("D", "A", "D"): "BCDA",
        ("D", "B", "C"): "ACBD", ("D", "B", "D"): "ACDB", ("D", "C", "D"): "ABDC",
    }

    @staticmethod
    def asked(moved, how, ref):
        rest = [s for s in "ABCD" if s != moved]
        rest.insert(rest.index(ref) + (how == "after"), moved)
        return "".join(rest)

    @staticmethod
    def books(active, behind):
        book = _Book("m.xlsx", sheets="ABCD")
        other = _Book("other.xlsx", app=book.app)
        book.sheets.active = book.sheets[active]
        book.app.books.active = other if behind else book
        return book, other

    def native(self, active, moved, before, behind):
        book, _other = self.books(active, behind)
        book.sheets[moved].api.Move(book.sheets[before].api)
        return "".join(book.order()), book

    def test_behind_the_fake_lands_every_move_where_excel_did(self):
        self.assertEqual(len(self.WRONG_BEHIND), 14)
        for active in "ABCD":
            for moved in "ABCD":
                for before in (s for s in "ABCD" if s != moved):
                    with self.subTest(active=active, moved=moved, before=before):
                        got, _book = self.native(active, moved, before, behind=True)
                        self.assertEqual(got, self.WRONG_BEHIND.get((active, moved, before),
                                                                    self.asked(moved, "before", before)))

    def test_in_front_the_fake_lands_every_move_where_asked(self):
        for active in "ABCD":
            for moved in "ABCD":
                for before in (s for s in "ABCD" if s != moved):
                    with self.subTest(active=active, moved=moved, before=before):
                        got, _book = self.native(active, moved, before, behind=False)
                        self.assertEqual(got, self.asked(moved, "before", before))

    def test_a_move_that_should_change_nothing_changes_something_behind(self):
        # A is already before B. With B showing and the workbook behind, Excel
        # put A after it — why the tool never makes a move it does not need.
        got, book = self.native("B", "A", "B", behind=True)
        self.assertEqual(got, "BACD")
        self.assertIs(book.app.books.active, book)              # ... and Move brought the workbook forward
        self.assertEqual(book.sheets.active.name, "A")          # showing the moved sheet

    def test_the_tool_lands_every_move_where_asked_whatever_sheet_is_showing(self):
        # All 96: every sheet showing, every sheet moved, before and after
        # every other — from a workbook that starts behind.
        for active in "ABCD":
            for moved in "ABCD":
                for how in ("before", "after"):
                    for ref in (s for s in "ABCD" if s != moved):
                        with self.subTest(active=active, moved=moved, how=how, ref=ref):
                            book, other = self.books(active, behind=True)
                            out = _Host(book).do_excel_sheet({"action": "move", "name": moved, how: ref})
                            self.assertEqual("".join(book.order()), self.asked(moved, how, ref), out)
                            self.assertNotIn("error", out)
                            self.assertEqual(book.exiled, [])
                            self.assertIs(book.app.books.active, other)     # the view is as it was
                            self.assertEqual(book.sheets.active.name, active)


class SheetMoveMacTests(_SheetCase):
    """The appscript shape. NOT yet run against a real Excel on a Mac."""
    PLATFORM = "darwin"

    def test_move_is_the_applescript_move_to_a_place(self):
        out = self.sheet("move", name="Data Sources", after="Cover")
        self.assertIn("Sheets: Cover, Data Sources, Spending, Documents", out)
        self.assertEqual([e for e in self.book.log if e[0] == "move"],
                         [("move", "Data Sources", "after", "Cover")])
        self.assertEqual(self.book.moves(), [])                 # never COM's Move

    def test_the_mac_adds_by_reference_so_no_index_is_consulted(self):
        self.book.tabs.insert(0, _Chart(self.book))
        self.assertFalse(ExcelMixin._excel_sheet_index_off(self.book, self.book.sheets["Documents"]))


# ── excel_zoom ───────────────────────────────────────────────────────────────

class ZoomToolTests(_PlatformCase):
    def setUp(self):
        super().setUp()
        self.book = _Book()
        self.data, self.second = self.book.sheets["Data"], self.book.sheets["Second"]
        self.h = _Host(self.book)

    def zoom(self, **params):
        return self.h.do_excel_zoom(params)

    def test_a_report_on_the_sheet_in_view_touches_nothing(self):
        self.assertEqual(self.zoom(), "Zoom on [fake.xlsx]Data: 100%.")
        self.assertEqual(self.book.log, [])

    def test_set_is_one_write_read_back(self):
        out = self.zoom(percent=85)
        self.assertEqual(out, "Set the zoom of [fake.xlsx]Data to 85% (before: 100%).")
        self.assertEqual(self.book.sets(), [("Zoom", 85)])
        self.assertEqual(self.data.panes.zoom, 85)
        self.assertEqual(self.zoom(), "Zoom on [fake.xlsx]Data: 85%.")

    def test_the_percent_is_taken_as_the_providers_send_it(self):
        for value in ("85", 85.0, " 85 "):
            book = _Book()
            self.assertIn("to 85%", _Host(book).do_excel_zoom({"percent": value}))

    def test_every_sheet_has_its_own_and_the_view_is_put_back(self):
        out = self.zoom(sheet="second", percent=70)
        self.assertEqual(out, "Set the zoom of [fake.xlsx]Second to 70% (before: 100%).")
        self.assertEqual((self.data.panes.zoom, self.second.panes.zoom), (100, 70))
        self.assertIs(self.book.sheets.active, self.data)
        self.assertEqual([name for kind, name in (e for e in self.book.log if e[0] == "activate")],
                         ["Second", "Data"])

    def test_the_workbook_in_front_is_neither_zoomed_nor_moved_from_the_front(self):
        other = _Book("other.xlsx", app=self.book.app)
        self.book.app.books.active = other
        self.book.activating_fronts = True
        self.zoom(sheet="Second", percent=70)
        self.assertEqual(other.sheets.active.panes.zoom, 100)
        self.assertEqual(other.sets(), [])
        self.assertIs(self.book.app.books.active, other)

    def test_asking_for_the_zoom_it_has_touches_nothing(self):
        self.zoom(percent=85)
        self.book.log.clear()
        self.assertEqual(self.zoom(percent=85), "Zoom on [fake.xlsx]Data is already 85% — nothing to do.")
        self.assertEqual(self.book.log, [])

    def test_a_bad_percent_never_reaches_excel(self):
        class NoExcel(ExcelMixin):
            def _excel_target(self, params):
                raise AssertionError("reached Excel")
        for value, why in ((9, "from 10 to 400"), (401, "from 10 to 400"), (85.5, "whole number"),
                           ("abc", "whole number"), (-5, "from 10 to 400")):
            with self.subTest(value=value):
                out = NoExcel().do_excel_zoom({"percent": value})
                self.assertTrue(out.startswith("excel_zoom error: 'percent' must be"))
                self.assertIn(why, out)

    def test_a_zoom_excel_did_not_take_is_not_claimed(self):
        self.book.zoom_stuck = True
        out = self.zoom(percent=85)
        self.assertEqual(out, "excel_zoom error: asked for 85% on [fake.xlsx]Data, and Excel reports 100%.")

    def test_excels_own_refusal_is_passed_on(self):
        self.book.fail_on = ("Zoom", 85)
        out = self.zoom(percent=85)
        self.assertEqual(out, "excel_zoom error: Unable to set the Zoom property of the Window class")

    def test_page_layout_view_keeps_a_zoom_of_its_own_and_the_result_says_so(self):
        self.zoom(percent=120)
        self.data.panes.view = 3
        self.assertEqual(self.zoom(), "Zoom on [fake.xlsx]Data: 100%. The sheet is in Page Layout view, "
                                      "which keeps a zoom of its own — Normal view's is not this one.")
        out = self.zoom(percent=60)
        self.assertTrue(out.startswith("Set the zoom of [fake.xlsx]Data to 60% (before: 100%). The sheet is"))
        self.data.panes.view = 1
        self.assertEqual(self.zoom(), "Zoom on [fake.xlsx]Data: 120%.")

    def test_frozen_panes_are_left_alone(self):
        self.h.do_excel_freeze_panes({"rows": 2})
        self.zoom(percent=90)
        self.assertEqual((self.data.panes.frozen, self.data.panes.rows, self.data.panes.zoom), (True, 2, 90))

    def test_a_hidden_or_missing_sheet_says_so(self):
        self.second.visible = False
        out = self.zoom(sheet="Second", percent=70)
        self.assertIn("could not show sheet 'Second' in fake.xlsx's window (is it hidden?)", out)
        self.assertEqual(self.data.panes.zoom, 100)             # NOT set on the sheet showing instead
        self.assertIn("Sheet 'Nope' not found in fake.xlsx", self.zoom(sheet="Nope", percent=70))

    def test_the_window_is_the_one_the_panes_use(self):
        # One way to the window, with its own-window rule and its checks.
        import inspect
        source = inspect.getsource(ExcelMixin.do_excel_zoom)
        self.assertIn("self._excel_pane_window(book, sht)", source)
        self.assertNotIn("ActiveWindow", source)
        self.assertNotIn("active_window", source)


class ZoomToolMacTests(_PlatformCase):
    """The appscript shape. NOT yet run against a real Excel on a Mac."""
    PLATFORM = "darwin"

    def test_zoom_goes_through_the_get_set_reference_with_the_workbook_brought_forward(self):
        book = _Book()
        other = _Book("other.xlsx", app=book.app)
        book.app.books.active = other
        out = _Host(book).do_excel_zoom({"sheet": "Second", "percent": 70})
        self.assertEqual(out, "Set the zoom of [fake.xlsx]Second to 70% (before: 100%).")
        self.assertEqual(book.log[0], ("book.activate", "fake.xlsx"))
        self.assertEqual(book.sheets["Second"].panes.zoom, 70)
        self.assertIs(book.app.books.active, other)
        self.assertEqual(other.sets(), [])

    def test_a_workbook_that_did_not_come_forward_is_never_zoomed_blind(self):
        book = _Book()
        other = _Book("other.xlsx", app=book.app)
        book.app.books.active = other
        book.front_stolen = 9
        out = _Host(book).do_excel_zoom({"percent": 70})
        self.assertIn("could not bring sheet 'Data' of fake.xlsx to the front", out)
        self.assertEqual(other.sheets.active.panes.zoom, 100)
        self.assertEqual(other.sets(), [])


# OPAQUE is what a COM error with no description prints as: a protected
# structure answers a Move with one, which is why the tool asks first.
assert OPAQUE.startswith("(-2147352567")

if __name__ == "__main__":
    unittest.main()
