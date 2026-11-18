"""excel_table and excel_freeze_panes (2026-11-18) — ExcelMixin's two layout tools.

No Excel and no xlwings here: the pure helpers run on the bare class, and the
two tools run over fakes shaped like xlwings' objects — a workbook, its sheets
and tables, and a window in COM shape (plain attributes) and in appscript
shape (.get() / .set()).

The fake window is not a call recorder alone. It BEHAVES the way a real Excel
was measured to that day, because every one of these is a way to get a freeze
that looks right and is wrong:

  * a freeze is taken from the window's current scroll position;
  * unfreezing alone leaves an ordinary split behind;
  * a freeze made over hidden rows settles at the FAR end of the hidden run,
    and reads one short of it while they stay hidden;
  * an absurd count raises nothing — Excel substitutes a mid-window freeze;
  * a frozen part larger than the window reads one short;
  * Page Layout view refuses the freeze after the old panes are gone.

So a change to the order of the calls, or to a guard, shows up as a wrong
OUTCOME. What only a real Excel can answer is exercised by hand in
tests/check_excel_live.py (its sections 10 and 11).
"""

import re
import unittest
from unittest import mock

import myagent.constants as c
import myagent.excel_mixin as excel_mixin
from myagent.excel_mixin import ExcelMixin

# What a COM error with no description looks like once str() has had it — the
# text Excel's refusal of an unknown style or a duplicate name really carries.
OPAQUE = "(-2147352567, 'Exception occurred.', (0, None, None, None, 0, -2147024809), None)"
ROW_HEIGHT = 15.0


# ── fakes ────────────────────────────────────────────────────────────────────

class _Count:
    def __init__(self, n):
        self.count = n


class _Rng:
    """What sht.range(...) returns: an address, a size, and — for the
    visibility probe — a row height / column width."""

    def __init__(self, sheet, r1, c1, r2, c2):
        self.sheet, self.r1, self.c1, self.r2, self.c2 = sheet, r1, c1, r2, c2

    @property
    def address(self):
        a = f"${ExcelMixin._excel_col_letter(self.c1)}${self.r1}"
        b = f"${ExcelMixin._excel_col_letter(self.c2)}${self.r2}"
        return a if (self.r1, self.c1) == (self.r2, self.c2) else f"{a}:{b}"

    @property
    def rows(self):
        return _Count(self.r2 - self.r1 + 1)

    @staticmethod
    def _span(first, last, hidden):
        states = {n in hidden for n in range(first, last + 1)}
        return 0.0 if states == {True} else (None if len(states) == 2 else ROW_HEIGHT)

    @property
    def row_height(self):
        self.sheet.probes += 1
        if self.sheet.unreadable:
            raise RuntimeError("row height unreadable")
        return self._span(self.r1, self.r2, self.sheet.hidden_rows)

    @property
    def column_width(self):
        self.sheet.probes += 1
        if self.sheet.unreadable:
            raise RuntimeError("column width unreadable")
        return self._span(self.c1, self.c2, self.sheet.hidden_cols)


def _corners(text):
    """'B8:F9' → (8, 2, 9, 6). Anything else raises the way Excel's does."""
    m = re.fullmatch(r"([A-Za-z]{1,3})(\d+)(?::([A-Za-z]{1,3})(\d+))?", text.replace("$", ""))
    if not m:
        raise RuntimeError(OPAQUE)
    c1, r1 = ExcelMixin._excel_col_number(m.group(1)), int(m.group(2))
    c2 = ExcelMixin._excel_col_number(m.group(3)) if m.group(3) else c1
    r2 = int(m.group(4)) if m.group(4) else r1
    return r1, c1, r2, c2


class _Table:
    """xlwings' Table: the attributes the tool reads and sets."""

    def __init__(self, sheet, name, addr, headers, data_rows=1, style="TableStyleMedium2"):
        self.sheet, self._name, self._display = sheet, name, name
        self.addr, self.headers, self.data_rows, self._style = addr, headers, data_rows, style
        self.show_table_style_row_stripes = self.show_autofilter = True
        self.show_table_style_column_stripes = False
        self.show_table_style_first_column = self.show_table_style_last_column = False
        self._totals = False
        self.api = _TableApi(self)

    @property
    def book(self):
        return self.sheet.book

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):          # the setter the tool must NOT use
        self.book.log.append(("name=", value))
        self._name = value

    @property
    def display_name(self):
        return self._display

    @display_name.setter
    def display_name(self, value):
        self.book.log.append(("display_name=", value))
        if value in self.book.refused_names:
            raise RuntimeError(OPAQUE)
        self._name = self._display = value

    @property
    def table_style(self):
        if self._style is None:     # xlwings reads .TableStyle.Name off an empty style
            raise AttributeError("'str' object has no attribute 'Name'")
        return self._style

    @table_style.setter
    def table_style(self, value):
        self.book.log.append(("style=", value))
        if value in self.book.unknown_styles:
            raise RuntimeError("(-2147352562, 'Invalid number of parameters.', None, None)")
        self._style = value

    @property
    def show_totals(self):
        return self._totals

    @show_totals.setter
    def show_totals(self, value):
        if value and not self._totals:      # the Total row is part of the table
            r1, c1, r2, c2 = _corners(self.addr)
            self.addr = _Rng(self.sheet, r1, c1, r2 + 1, c2).address.replace("$", "")
        self._totals = bool(value)

    @property
    def range(self):
        return _Rng(self.sheet, *_corners(self.addr))

    @property
    def header_row_range(self):
        if self.headers is None:
            return None
        value = self.headers[0] if len(self.headers) == 1 else list(self.headers)
        return type("Header", (), {"value": value, "column": _corners(self.addr)[1]})()

    @property
    def data_body_range(self):
        if not self.data_rows:
            return None
        return type("Body", (), {"rows": _Count(self.data_rows)})()

    def resize(self, rng):
        self.book.log.append(("resize", rng.address.replace("$", "")))
        if self.book.refuse_resize:
            raise RuntimeError("This range won't work because it doesn't align with the existing table.")
        self.addr = rng.address.replace("$", "")
        self.data_rows = rng.rows.count - 1


class _TableApi:
    def __init__(self, table):
        self.table = table

    def _drop(self, how):
        book = self.table.book
        book.log.append((how, self.table.name))
        if not book.unlist_sticks:
            self.table.sheet.tables.remove(self.table)

    def Unlist(self):                   # COM
        self._drop("Unlist")

    def convert_to_range(self):         # appscript
        self._drop("convert_to_range")


class _Tables(list):
    def __init__(self, sheet):
        super().__init__()
        self.sheet = sheet

    def add(self, source=None, table_style_name=None, **kwargs):
        book = self.sheet.book
        book.log.append(("add", source.address.replace("$", ""), table_style_name, dict(kwargs)))
        if book.add_error:
            raise RuntimeError(book.add_error)
        if table_style_name in book.unknown_styles:
            raise RuntimeError(OPAQUE)
        book.auto += 1
        r1, c1, r2, c2 = source.r1, source.c1, source.r2, source.c2
        header_only = r1 == r2
        addr = _Rng(self.sheet, r1, c1, r2 + (1 if header_only else 0), c2).address.replace("$", "")
        headers = [f"Column{i + 1}" for i in range(c2 - c1 + 1)]
        table = _Table(self.sheet, f"Table{book.auto}", addr, headers,
                       data_rows=0 if header_only else r2 - r1, style=table_style_name)
        self.append(table)
        return table


class _Panes:
    """One sheet's pane state, behaving as a real Excel was measured to on
    2026-11-18 (see the module docstring). `rows` / `cols` are where the split
    IS; what SplitRow READS is another matter."""

    def __init__(self, sheet):
        self.sheet = sheet
        self.frozen = False
        self.rows = self.cols = 0
        self.top_row = self.top_col = 1         # first row / column of the window — of the frozen part, once frozen
        self.scroll_row = self.scroll_col = 1   # the scrollable part's, once frozen
        self.view = 1
        self.zoom = self.layout_zoom = 100      # Normal view and Page Layout view keep one each

    def _reads(self, count, top, hidden):
        if not count:
            return 0
        if self.frozen and (top + count - 1 in hidden or count > self.sheet.book.window_size):
            return count - 1
        return count

    def get(self, key):
        if key == "FreezePanes":
            return self.frozen
        if key == "SplitRow":
            return self._reads(self.rows, self.top_row, self.sheet.hidden_rows)
        if key == "SplitColumn":
            return self._reads(self.cols, self.top_col, self.sheet.hidden_cols)
        # Scrolling addresses the scrollable part only along an axis that HAS
        # a frozen part; along the other the whole window moves.
        if key == "ScrollRow":
            return self.scroll_row if self.frozen and self.rows else self.top_row
        if key == "ScrollColumn":
            return self.scroll_col if self.frozen and self.cols else self.top_col
        if key == "View":
            return self.view
        if key == "Zoom":                       # COM answers a float
            return float(self.layout_zoom if self.view == 3 else self.zoom)
        raise AttributeError(key)

    def put(self, key, value):
        if key == "FreezePanes":
            if not value:
                self.frozen = False             # the split stays: an ordinary split now
                return
            if self.view == 3:
                raise RuntimeError("Unable to set the FreezePanes property of the Window class")
            if not (self.rows or self.cols):    # nothing to freeze at: Excel picks mid-window
                self.rows, self.cols = 16, 10
            while self.rows and self.top_row + self.rows in self.sheet.hidden_rows:
                self.rows += 1                  # the far end of the hidden run
            while self.cols and self.top_col + self.cols in self.sheet.hidden_cols:
                self.cols += 1
            self.frozen = True
            self.scroll_row = self.top_row + self.rows
            self.scroll_col = self.top_col + self.cols
        elif key in ("SplitRow", "SplitColumn"):
            if value < 0 or value >= 2_000_000:
                raise RuntimeError(f"Unable to set the {key} property of the Window class")
            taken = 0 if value > 1_000_000 else value       # an absurd count is silently dropped
            if key == "SplitRow":
                self.rows = taken
            else:
                self.cols = taken
        elif key == "ScrollRow":
            if self.frozen and self.rows:
                self.scroll_row = value
            else:
                self.top_row = value
        elif key == "ScrollColumn":
            if self.frozen and self.cols:
                self.scroll_col = value
            else:
                self.top_col = value
        elif key == "View":
            self.view = value
        elif key == "Zoom":
            if not 10 <= value <= 400:
                raise RuntimeError("Unable to set the Zoom property of the Window class")
            if self.sheet.book.zoom_stuck:
                return
            if self.view == 3:
                self.layout_zoom = int(value)   # a fraction is dropped, silently
            else:
                self.zoom = int(value)
        else:
            raise AttributeError(key)


class _ComWindow:
    """book.api.Windows(1): plain attributes; shows the workbook's active sheet."""

    def __init__(self, book):
        object.__setattr__(self, "book", book)

    @property
    def _panes(self):
        return self.book.sheets.active.panes

    def __getattr__(self, key):
        return self._panes.get(key)

    def __setattr__(self, key, value):
        self.book.log.append(("set", key, value))
        if self.book.fail_on == (key, value):
            raise RuntimeError(f"Unable to set the {key} property of the Window class")
        self._panes.put(key, value)

    @property
    def ActiveSheet(self):
        return type("Shown", (), {"Name": self.book.sheets.active.name})()

    def Panes(self, index):
        panes = self._panes
        return type("Pane", (), {"ScrollRow": panes.top_row, "ScrollColumn": panes.top_col})()


class _MacProp:
    def __init__(self, window, key):
        self.window, self.key = window, key

    def get(self):
        value = getattr(self.window, self.key)
        if self.key == "View":      # appscript answers with a keyword
            return "k.page_layout_view" if value == 3 else "k.normal_view"
        return value

    def set(self, value):
        setattr(self.window, self.key, value)


class _MacWindow:
    """app.api.active_window: every property a reference with .get() / .set()."""
    NAMES = {"freeze_panes": "FreezePanes", "split_row": "SplitRow", "split_column": "SplitColumn",
             "scroll_row": "ScrollRow", "scroll_column": "ScrollColumn", "view": "View", "zoom": "Zoom"}

    def __init__(self, window):
        self._window = window

    def __getattr__(self, name):
        if name not in self.NAMES:      # appscript: "Unknown property, element or command"
            raise AttributeError(name)
        return _MacProp(self._window, self.NAMES[name])


_NO_ARGUMENT = object()


class _TabApi:
    """A sheet's native object in both shapes: COM's Index / Name / Move, and
    appscript's before / after / move. Move BEHAVES as a real Excel was
    measured to on 2026-11-18 (tests/test_excel_sheet_zoom.py):

      * without a real sheet as its first argument — nothing given, or the
        target passed as After — the sheet LEAVES the workbook for a new one;
      * in a workbook that is not in front, the landing place allows for the
        removal of that workbook's ACTIVE sheet instead of the moved one, so
        the sheet lands one tab off whenever the two lie on opposite sides
        of the destination (312 moves behind, every one as this says);
      * it shows the moved sheet and brings its workbook forward.
    """

    def __init__(self, tab):
        self.tab = tab

    @property
    def Index(self):                # among ALL the tabs, chart sheets included
        return self.tab.book.tabs.index(self.tab) + 1

    @property
    def Name(self):
        return self.tab.name

    @property
    def before(self):
        return ("before", self.tab)

    @property
    def after(self):
        return ("after", self.tab)

    def Move(self, before=_NO_ARGUMENT, after=_NO_ARGUMENT):
        book, tab = self.tab.book, self.tab
        book.log.append(("Move", tab.name, getattr(getattr(before, "tab", None), "name", None),
                         getattr(getattr(after, "tab", None), "name", None)))
        if book.protected:
            raise RuntimeError(OPAQUE)
        if after is not _NO_ARGUMENT or not isinstance(before, _TabApi):
            book.tabs.remove(tab)           # out, into a new workbook
            book.exiled.append(tab.name)
            book.sync()
            return
        if book.move_stuck:
            return
        tabs = book.tabs
        target = tabs.index(before.tab) + 1                 # 1-based: "before index `target`"
        # Taking a sheet out from the left of the target shifts the target one
        # down. In front Excel asks that of the moved sheet; behind, of the
        # workbook's active sheet — the measured slip.
        leaving = tab if book.app.books.active is book else book.sheets.active
        final = target - 1 if tabs.index(leaving) + 1 < target else target
        tabs.remove(tab)
        tabs.insert(final - 1, tab)
        book.sync()
        if isinstance(tab, _Sheet) and tab.visible:
            book.sheets.active = tab
        book.app.books.active = book

    def move(self, to=None):                # appscript: move … to before / after …
        how, ref = to
        book, tab = self.tab.book, self.tab
        book.log.append(("move", tab.name, how, ref.name))
        book.tabs.remove(tab)
        slot = book.tabs.index(ref)
        book.tabs.insert(slot if how == "before" else slot + 1, tab)
        book.sync()


class _Chart:
    """A chart sheet: a tab that is not a worksheet. It is in the workbook's
    Sheets and has an Index, and xlwings' book.sheets does not list it."""

    def __init__(self, book, name="Chart1"):
        self.book, self.name = book, name

    @property
    def api(self):
        return _TabApi(self)


class _Sheet:
    def __init__(self, book, name):
        self.book, self.name = book, name
        self.tables = _Tables(self)
        self.panes = _Panes(self)
        self.hidden_rows, self.hidden_cols = set(), set()
        self.visible = True
        self.unreadable = False     # the visibility probe raises
        self.probes = 0

    @property
    def api(self):
        return _TabApi(self)

    def range(self, first, last=None):
        if isinstance(first, str):
            return _Rng(self, *_corners(first))
        return _Rng(self, first[0], first[1], last[0], last[1])

    def activate(self):
        self.book.log.append(("activate", self.name))
        if not self.visible:
            return                  # a hidden sheet: nothing raised, nothing changed
        self.book.sheets.active = self
        if self.book.activating_fronts:
            self.book.app.books.active = self.book


class _Sheets(list):
    """xlwings' book.sheets: the worksheets, in tab order."""
    active = None

    def __init__(self, book, sheets):
        super().__init__(sheets)
        self.book = book

    def __getitem__(self, key):
        if isinstance(key, str):
            return next(s for s in self if s.name == key)
        return super().__getitem__(key)

    @property
    def count(self):
        return len(self)

    def add(self, name=None, before=None, after=None):
        """As xlwings does it on Windows (its source, and a real Excel on
        2026-11-18):

          * the sheet is made FIRST and named second, so a name Excel refuses
            leaves a sheet behind under its automatic one;
          * `before` is a plain Sheets.Add(Before=…);
          * `after` works the place out from Worksheet.Index — which counts
            every tab — over a collection that holds worksheets only. With a
            chart sheet ahead the new sheet lands a tab late, and beside the
            last worksheets ANOTHER sheet is moved: after C in
            Chart1, A, B, C, D left Chart1, A, B, new, D, C.
        """
        book = self.book
        book.log.append(("sheets.add", name, getattr(before, "name", None), getattr(after, "name", None)))
        if book.protected:
            raise RuntimeError("Add method of Sheets class failed")
        if name is not None and name.lower() in (s.name.lower() for s in self):
            raise ValueError("Sheet named '%s' already present in workbook" % name)
        book.auto_sheet += 1
        new = _Sheet(book, f"Sheet{book.auto_sheet}")
        tabs = book.tabs
        shown = new                         # a new sheet is the sheet in view
        if book.add_misplaces:              # a stand-in: a placement that missed for a reason not yet seen
            tabs.append(new)
        elif before is not None:
            tabs.insert(tabs.index(before), new)
        elif after is not None:
            new_index = tabs.index(after) + 2               # after.xl.Index + 1
            if new_index > len(self):                       # ... against Worksheets.Count
                tabs.insert(tabs.index(after), new)         # Add(Before=after)
                book.sync()
                last, second_last = self[-1], self[-2]      # Worksheets(Count).Move(Before=Worksheets(Count - 1))
                tabs.remove(last)
                tabs.insert(tabs.index(second_last), last)
                book.sync()
                shown = self[-1]                            # Worksheets(Count).Activate()
                book.app.books.active = book                # ... which brings the workbook forward
            else:
                tabs.insert(tabs.index(self[new_index - 1]), new)   # Add(Before=Worksheets(Index + 1))
        else:
            tabs.insert(tabs.index(self.active), new)
        book.sync()
        self.active = shown
        if name is not None:
            if ExcelMixin._excel_sheet_name_problem(name):
                raise RuntimeError("You typed an invalid name for a sheet or chart.")
            new.name = name
        return new


class _Books(list):
    active = None

    def __getitem__(self, key):
        if isinstance(key, str):
            return next(b for b in self if b.name == key)
        return super().__getitem__(key)


class _App:
    def __init__(self):
        self.books = _Books()

    @property
    def api(self):
        return type("AppApi", (), {"active_window": _MacWindow(self.books.active.window)})()


class _AllTabs:
    """book.api.Sheets: every tab, chart sheets included, by 1-based index."""

    def __init__(self, book):
        self.book = book

    @property
    def Count(self):
        return len(self.book.tabs)

    def __call__(self, index):
        return self.book.tabs[index - 1].api


class _BookApi:
    def __init__(self, book):
        self.book = book

    def Windows(self, index):
        return self.book.window

    @property
    def Sheets(self):
        return _AllTabs(self.book)

    @property
    def ProtectStructure(self):
        return self.book.protected


class _Book:
    def __init__(self, name="fake.xlsx", sheets=("Data", "Second"), app=None):
        self.name = name
        self.log = []
        self.auto = 0
        self.names = []                     # defined names: objects with .name
        self.unknown_styles = {"NoSuchStyle"}
        self.refused_names = set()
        self.add_error = None
        self.refuse_resize = self.unlist_sticks = False
        self.window_size = 32               # rows (and columns) the window can show
        self.activating_fronts = False      # showing a sheet brings the workbook forward
        self.fail_on = None                 # (property, value) whose write raises
        self.tabs = [_Sheet(self, n) for n in sheets]      # every tab; a _Chart may join them
        self.sheets = _Sheets(self, self.tabs)
        self.sheets.active = self.sheets[0]
        self.auto_sheet = len(self.tabs)
        self.protected = False              # the workbook's structure
        self.add_misplaces = False          # sheets.add puts the new sheet last, whatever was asked
        self.move_stuck = False             # Move raises nothing and moves nothing
        self.zoom_stuck = False             # a zoom write raises nothing and changes nothing
        self.front_stolen = 0               # how many activate() calls another workbook pre-empts
        self.exiled = []                    # sheets that left for a new workbook
        self.window = _ComWindow(self)
        self.api = _BookApi(self)
        self.app = app or _App()
        self.app.books.append(self)
        if self.app.books.active is None:
            self.app.books.active = self

    def sync(self):
        """book.sheets follows the tabs: the worksheets among them, in order."""
        self.sheets[:] = [t for t in self.tabs if isinstance(t, _Sheet)]

    def order(self):
        return [t.name for t in self.tabs]

    def moves(self):
        """The native Move calls: (moved, Before, After)."""
        return [e[1:] for e in self.log if e[0] == "Move"]

    def activate(self):
        self.log.append(("book.activate", self.name))
        if self.front_stolen:
            self.front_stolen -= 1          # someone clicked another workbook just then
            return
        self.app.books.active = self

    def sets(self):
        """The window writes, in order."""
        return [(key, value) for kind, key, value in (e for e in self.log if e[0] == "set")]


class _Host(ExcelMixin):
    def __init__(self, book):
        self.book = book

    def _excel_book(self, workbook=None):
        return self.book


def _named(name):
    return type("Defined", (), {"name": name})()


def _table(book, name="TableDOCS", sheet="Data", addr="B8:F9",
           headers=("Date", "Category1", "Category2", "Category3", "Comments"), **kwargs):
    sht = book.sheets[sheet]
    table = _Table(sht, name, addr, None if headers is None else list(headers), **kwargs)
    sht.tables.append(table)
    return table


class _PlatformCase(unittest.TestCase):
    """The tools branch on sys.platform for the two native shapes, so each
    test fixes it — the suite runs on Windows and on the Mac."""
    PLATFORM = "win32"

    def setUp(self):
        patcher = mock.patch.object(excel_mixin.sys, "platform", self.PLATFORM)
        patcher.start()
        self.addCleanup(patcher.stop)


# ── pure helpers ─────────────────────────────────────────────────────────────

class NameRuleTests(unittest.TestCase):
    """_excel_table_name_problem against what Excel itself answered on
    2026-11-18 when each name was given to a table's display-name setter —
    the setter the tool uses. True = Excel took it."""

    EXCEL_SAID = {
        "TableDOCS": True, "Good_Name2": True, "_u": True, "\\b": True, "x.y": True,
        "a?b": True, "ÉtéTable": True, "y" * 255: True,
        "XFE1": True,           # one column past the sheet's last (XFD): no reference
        "A1048577": True,       # one row past its last
        "Has Space": False, "Table 1": False, "1abc": False, "Tab-le": False,
        "A1": False, "TAB1": False, "FY2025": False,      # cell references, A1 style
        "R1C1": False, "RC": False, "R2C": False, "R": False, "C": False,
        "y" * 256: False, "": False,
    }

    def test_the_rule_agrees_with_excel_on_every_measured_name(self):
        for name, taken in self.EXCEL_SAID.items():
            with self.subTest(name=name[:20]):
                self.assertEqual(ExcelMixin._excel_table_name_problem(name) is None, taken)

    def test_each_refusal_says_why(self):
        problem = ExcelMixin._excel_table_name_problem
        self.assertIn("space", problem("Has Space"))
        self.assertIn("underscore", problem("Has Space"))        # and what to do instead
        self.assertIn("start with", problem("1abc"))
        self.assertIn("-", problem("Tab-le"))
        self.assertIn("cell reference", problem("FY2025"))
        self.assertIn("cell reference", problem("T2"))           # the short name that looks so harmless
        self.assertIn("255", problem("y" * 256))
        self.assertIn("empty", problem(""))

    def test_cell_references_in_both_notations(self):
        ref = ExcelMixin._excel_is_cell_reference
        for name in ("A1", "a1", "XFD1048576", "Q4", "T2", "R5", "C7", "R1C1", "r1c1", "RC", "Rc",
                     "R2C", "RC3", "R", "C", "r", "c"):
            self.assertTrue(ref(name), name)
        for name in ("XFE1", "A1048577", "A0", "AAAA1", "Table1", "TableDOCS", "R0C1", "R1C0", ""):
            self.assertFalse(ref(name), name)

    def test_col_number_inverts_col_letter(self):
        self.assertEqual(ExcelMixin._excel_col_number("A"), 1)
        self.assertEqual(ExcelMixin._excel_col_number("xfd"), 16384)
        for n in (1, 26, 27, 52, 53, 702, 703, 16384):
            self.assertEqual(ExcelMixin._excel_col_number(ExcelMixin._excel_col_letter(n)), n)


class PaneValueTests(unittest.TestCase):
    def test_a_count_is_taken_as_the_providers_send_it(self):
        count = ExcelMixin._excel_pane_count
        for value in (8, 8.0, "8", " 8 "):
            self.assertEqual(count(value, "rows", 1_048_576), 8)
        self.assertEqual(count(0, "rows", 1_048_576), 0)
        self.assertEqual(count(1_048_575, "rows", 1_048_576), 1_048_575)

    def test_anything_else_is_refused_naming_the_parameter(self):
        count = ExcelMixin._excel_pane_count
        for value in (-1, 2.5, "eight", "", None, True, [8], float("nan"), float("inf"), 1_048_576):
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as ctx:
                    count(value, "rows", 1_048_576)
                self.assertIn("'rows'", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            count(16_384, "columns", 16_384)
        self.assertIn("16,384", str(ctx.exception))

    def test_zero_is_a_value_not_an_absence(self):
        given = ExcelMixin._excel_given
        self.assertTrue(given(0))
        self.assertTrue(given("0"))
        self.assertFalse(given(None))
        self.assertFalse(given(""))

    def test_the_frozen_part_in_words(self):
        words = ExcelMixin._excel_pane_words
        self.assertEqual(words(1, 0), "top row")
        self.assertEqual(words(8, 0), "top 8 rows")
        self.assertEqual(words(0, 1), "left column")
        self.assertEqual(words(8, 2), "top 8 rows and left 2 columns")
        self.assertEqual(words(0, 0), "")

    def test_a_state_in_words(self):
        text = ExcelMixin._excel_pane_text
        self.assertEqual(text({"frozen": True, "rows": 8, "columns": 0}), "frozen — top 8 rows kept in view")
        self.assertEqual(text({"frozen": True, "rows": 0, "columns": 0}), "frozen")
        self.assertEqual(text({"frozen": False, "rows": 5, "columns": 0}), "split but not frozen (below row 5)")
        self.assertEqual(text({"frozen": False, "rows": 1, "columns": 1}),
                         "split but not frozen (below row 1, right of column A)")
        self.assertEqual(text({"frozen": False, "rows": 0, "columns": 0}), "not frozen")


class PaneLandedTests(unittest.TestCase):
    """The read-backs are a real Excel's, 2026-11-18."""

    @staticmethod
    def state(frozen, rows, columns):
        return {"frozen": frozen, "rows": rows, "columns": columns}

    def test_what_was_asked_for(self):
        landed = ExcelMixin._excel_pane_landed
        self.assertTrue(landed(self.state(True, 8, 1), 8, 1))
        self.assertTrue(landed(self.state(True, 1, 0), 1, 0))
        self.assertTrue(landed(self.state(False, 0, 0), 0, 0))

    def test_a_frozen_part_larger_than_the_window_reads_one_short(self):
        landed = ExcelMixin._excel_pane_landed
        self.assertTrue(landed(self.state(True, 39, 5), 40, 5))
        self.assertTrue(landed(self.state(True, 499, 0), 500, 0))
        self.assertTrue(landed(self.state(True, 0, 99), 0, 100))

    def test_the_silent_mid_window_substitute_is_caught(self):
        # 1,048,575 rows raised nothing and became 16 rows by 10 columns.
        self.assertFalse(ExcelMixin._excel_pane_landed(self.state(True, 16, 10), 1_048_575, 0))

    def test_a_freeze_that_took_hidden_rows_in_is_caught(self):
        # 8 rows over hidden rows 9-20 read 19: larger than asked is not what was asked.
        self.assertFalse(ExcelMixin._excel_pane_landed(self.state(True, 19, 0), 8, 0))

    def test_clear_evidence_of_failure(self):
        landed = ExcelMixin._excel_pane_landed
        self.assertFalse(landed(self.state(False, 8, 0), 8, 0))     # the flag
        self.assertFalse(landed(self.state(True, 8, 3), 8, 0))      # a split nobody asked for
        self.assertFalse(landed(self.state(True, 0, 1), 1, 1))      # one row asked, none frozen
        self.assertFalse(landed(self.state(True, 8, 0), 0, 0))      # still frozen
        self.assertFalse(landed(self.state(False, 8, 0), 0, 0))     # unfrozen, the split left behind


class TableLineTests(unittest.TestCase):
    INFO = {"name": "TableDOCS", "display_name": "TableDOCS", "sheet": "Documents", "range": "B8:F9",
            "style": "TableStyleMedium2", "data_rows": 1, "options": ["banded rows", "filter buttons"],
            "columns": [("B", "Date"), ("C", "Category1"), ("F", "Comments")]}

    def test_the_line_names_columns_by_letter_and_header(self):
        self.assertEqual(
            ExcelMixin._excel_table_line(self.INFO),
            "• TableDOCS — Documents!B8:F9, 1 data row, style TableStyleMedium2; banded rows, filter buttons\n"
            "  columns: B=Date, C=Category1, F=Comments")

    def test_the_variants(self):
        line = ExcelMixin._excel_table_line
        self.assertIn("10002 data rows", line(dict(self.INFO, data_rows=10002)))
        self.assertIn("0 data rows, no style\n", line(dict(self.INFO, data_rows=0, style=None, options=[])))
        self.assertIn("columns: (header row hidden)", line(dict(self.INFO, columns=None)))
        self.assertIn("• Has Space (in formulas: Has_Space) —",
                      line(dict(self.INFO, name="Has Space", display_name="Has_Space")))

    def test_a_wide_table_is_summed_up(self):
        cols = [(ExcelMixin._excel_col_letter(i), f"h{i}") for i in range(1, 53)]
        out = ExcelMixin._excel_table_line(dict(self.INFO, columns=cols))
        self.assertIn("AN=h40, … +12 more", out)
        self.assertNotIn("h41", out)


class ReasonTests(unittest.TestCase):
    def test_a_described_error_is_passed_on_without_its_full_stop(self):
        self.assertEqual(ExcelMixin._excel_reason(RuntimeError("A table can't overlap another table. ")),
                         "A table can't overlap another table")

    def test_a_bare_tuple_of_numbers_becomes_the_fallback(self):
        self.assertEqual(ExcelMixin._excel_reason(RuntimeError(OPAQUE)), "Excel gave no reason")
        self.assertEqual(ExcelMixin._excel_reason(RuntimeError(OPAQUE), "no such style"), "no such style")
        self.assertEqual(ExcelMixin._excel_reason(RuntimeError(""), ""), "")


# ── the schemas ──────────────────────────────────────────────────────────────

class SchemaTests(unittest.TestCase):
    @staticmethod
    def schema(name):
        return next(t for t in c.EXCEL_TOOLS if t["name"] == name)["input_schema"]

    def test_the_action_set_is_one_tuple_for_the_schema_and_the_guard(self):
        self.assertEqual(self.schema("excel_table")["properties"]["action"]["enum"],
                         list(c.EXCEL_TABLE_ACTIONS))
        self.assertIs(ExcelMixin.TABLE_ACTIONS, c.EXCEL_TABLE_ACTIONS)
        self.assertIs(ExcelMixin.TABLE_OPTIONS, c.EXCEL_TABLE_OPTIONS)

    def test_the_display_options_are_exactly_the_schemas_booleans(self):
        props = self.schema("excel_table")["properties"]
        booleans = {k for k, v in props.items() if v["type"] == "boolean"}
        self.assertEqual(booleans, set(c.EXCEL_TABLE_OPTIONS))
        for param, (_attr, _words, text) in c.EXCEL_TABLE_OPTIONS.items():
            self.assertEqual(props[param]["description"], text)

    @unittest.skipIf(excel_mixin.xw is None, "xlwings not installed")
    def test_every_option_names_a_real_xlwings_table_attribute(self):
        table = excel_mixin.xw.main.Table
        for param, (attr, _words, _text) in c.EXCEL_TABLE_OPTIONS.items():
            self.assertIsInstance(getattr(table, attr, None), property, f"{param} → {attr}")
        self.assertIsInstance(table.display_name, property)

    def test_freeze_panes_takes_two_optional_numbers(self):
        schema = self.schema("excel_freeze_panes")
        self.assertEqual(schema["required"], [])
        self.assertEqual({k: v["type"] for k, v in schema["properties"].items()},
                         {"workbook": "string", "sheet": "string", "rows": "number", "columns": "number"})

    def test_every_excel_tool_has_its_method_and_stays_sequential(self):
        # Dispatch is the dynamic do_<name> lookup of both apps and the MCP
        # server, so a schema without a method would be an "Unknown Excel tool".
        for tool in c.EXCEL_TOOLS:
            name = tool["name"]
            with self.subTest(tool=name):
                self.assertTrue(callable(getattr(ExcelMixin, f"do_{name}", None)))
                self.assertNotIn(name, c.PARALLEL_SAFE_TOOLS)       # one shared COM apartment
                self.assertRegex(name, r"^[a-zA-Z0-9_-]{1,64}$")    # every provider's name rule
        self.assertEqual(len(c.EXCEL_TOOLS), 12)


# ── excel_table ──────────────────────────────────────────────────────────────

class TableToolTests(_PlatformCase):
    def setUp(self):
        super().setUp()
        self.book = _Book()
        self.h = _Host(self.book)

    def run_table(self, **params):
        return self.h.do_excel_table(params)

    def added(self):
        return [e for e in self.book.log if e[0] == "add"]

    def test_a_bad_action_is_named_before_anything_is_looked_up(self):
        class NoExcel(ExcelMixin):
            def _excel_book(self, workbook=None):
                raise AssertionError("looked for a workbook")
        out = NoExcel().do_excel_table({"action": "frobnicate", "name": "x"})
        self.assertEqual(out, "excel_table error: unknown action 'frobnicate'. "
                              "Use list, create, update, convert_to_range.")

    def test_list_reports_every_table_or_one_sheets(self):
        self.assertEqual(self.run_table(action="list"), "No tables in fake.xlsx.")
        _table(self.book)
        _table(self.book, "Other", sheet="Second", addr="A1:B3", headers=("k", "v"), data_rows=2)
        out = self.run_table(action="list")
        self.assertTrue(out.startswith("Tables in fake.xlsx:\n• TableDOCS — Data!B8:F9, 1 data row"))
        self.assertIn("columns: B=Date, C=Category1, D=Category2, E=Category3, F=Comments", out)
        self.assertIn("• Other — Second!A1:B3, 2 data rows", out)
        one = self.run_table(action="list", sheet="second")          # the real name, whatever the case
        self.assertTrue(one.startswith("Tables in sheet 'Second' of fake.xlsx:\n• Other"))
        self.assertNotIn("TableDOCS", one)
        self.assertEqual(self.run_table(action="list", sheet="Data")[:24], "Tables in sheet 'Data' o")
        self.assertIn("Sheet 'Nope' not found", self.run_table(action="list", sheet="Nope"))

    def test_list_survives_a_table_with_no_style_and_no_header_row(self):
        _table(self.book, style=None, headers=None, data_rows=0)
        out = self.run_table(action="list")
        self.assertIn("0 data rows, no style", out)
        self.assertIn("columns: (header row hidden)", out)

    def test_create_makes_names_and_reports_the_table(self):
        out = self.run_table(action="create", sheet="Data", range="B8:F9", name="TableDOCS")
        self.assertTrue(out.startswith("Created table 'TableDOCS' in fake.xlsx.\n• TableDOCS — Data!B8:F9"))
        self.assertEqual(self.added(), [("add", "B8:F9", "TableStyleMedium2", {})])    # Excel's own default style
        # Named through display_name — the setter Excel validates — never .name,
        # which takes anything and gives the table another name for formulas.
        self.assertIn(("display_name=", "TableDOCS"), self.book.log)
        self.assertFalse([e for e in self.book.log if e[0] == "name="])

    def test_create_passes_the_style_in_with_the_creation_and_sets_the_options(self):
        out = self.run_table(action="create", range="H8:J12", name="Docs_2", style="TableStyleLight9",
                             banded_rows=False, first_column=True, filter_buttons=False)
        self.assertEqual(self.added(), [("add", "H8:J12", "TableStyleLight9", {})])
        self.assertIn("(banded_rows=false, first_column=true, filter_buttons=false)", out)
        table = self.book.sheets["Data"].tables[0]
        self.assertFalse(table.show_table_style_row_stripes)
        self.assertTrue(table.show_table_style_first_column)
        self.assertFalse(table.show_autofilter)
        self.assertIn("style TableStyleLight9; first column emphasised\n", out)

    def test_create_without_a_name_keeps_excels(self):
        out = self.run_table(action="create", range="A1:B3")
        self.assertTrue(out.startswith("Created table 'Table1' in fake.xlsx."))
        self.assertFalse([e for e in self.book.log if e[0] == "display_name="])

    def test_a_name_excel_would_refuse_is_refused_before_a_table_exists(self):
        for name, why in (("Has Space", "space"), ("FY2025", "cell reference"), ("1st", "start with")):
            with self.subTest(name=name):
                out = self.run_table(action="create", range="B8:F9", name=name)
                self.assertIn(f"'{name}' cannot be a table name", out)
                self.assertIn(why, out)
                self.assertIn("Nothing was created.", out)
        self.assertEqual(self.added(), [])

    def test_a_name_already_in_use_is_refused_before_a_table_exists(self):
        # xlwings creates first and names second: a duplicate used to leave
        # an orphan 'Table4' behind, with an error that said nothing.
        _table(self.book)
        out = self.run_table(action="create", range="H8:I9", name="tabledocs")
        self.assertEqual(out, "excel_table error: a table named 'TableDOCS' already exists (Data!B8:F9). "
                              "Nothing was created.")
        self.book.names.append(_named("Budget"))
        out = self.run_table(action="create", range="H8:I9", name="budget")
        self.assertIn("already has a defined name 'Budget'", out)
        self.assertEqual(self.added(), [])

    def test_unreadable_defined_names_do_not_block_a_create(self):
        class Unreadable:
            def __iter__(self):
                raise RuntimeError("names unreadable")
        self.book.names = Unreadable()
        self.assertTrue(self.run_table(action="create", range="B8:F9", name="Docs").startswith("Created"))

    def test_create_needs_a_range_excel_can_read(self):
        self.assertIn("'range' is required", self.run_table(action="create", name="Docs"))
        out = self.run_table(action="create", range="not a range", name="Docs")
        self.assertIn("'not a range' is not a range of sheet 'Data' that Excel can read", out)
        self.assertNotIn("-2147352567", out)        # no bare COM tuple
        self.assertEqual(self.added(), [])

    def test_an_unknown_style_is_named_as_the_likely_cause(self):
        out = self.run_table(action="create", range="B8:F9", name="Docs", style="NoSuchStyle")
        self.assertIn("gave no reason — most likely it does not know the style 'NoSuchStyle'", out)
        self.assertIn("TableStyleMedium1-28", out)
        self.assertIn("Nothing was created.", out)
        self.assertEqual(self.book.sheets["Data"].tables, [])

    def test_excels_own_reason_is_passed_on(self):
        self.book.add_error = "A table can't overlap another table. "
        out = self.run_table(action="create", range="B8:F9", name="Docs", style="TableStyleDark1")
        self.assertIn("could not make a table of Data!B8:F9: A table can't overlap another table. Nothing", out)
        self.assertNotIn("style 'TableStyleDark1'", out)     # the reason is known: no guessing at the style

    def test_a_name_refused_after_creation_is_said_and_the_table_kept(self):
        self.book.refused_names = {"Odd_Name"}      # past the pre-check, refused by Excel all the same
        out = self.run_table(action="create", range="B8:F9", name="Odd_Name")
        self.assertTrue(out.startswith("Created table 'Table1' in fake.xlsx."))
        self.assertIn("⚠ Excel refused the name 'Odd_Name' (Excel gave no reason)", out)
        self.assertIn("action=update", out)
        self.assertIn("action=convert_to_range", out)
        self.assertEqual(len(self.book.sheets["Data"].tables), 1)

    def test_a_table_that_is_not_the_range_asked_for_says_why(self):
        out = self.run_table(action="create", range="L8:M8", name="Empty_1")
        self.assertIn("The table is L8:M9, not the L8:M8 asked for: "
                      "a header row alone gets one empty data row.", out)
        out = self.run_table(action="create", range="P8:Q9", name="With_Total", total_row=True)
        self.assertIn("The table is P8:Q10, not the P8:Q9 asked for: the Total row is part of the table.", out)
        plain = self.run_table(action="create", range="S8:T9", name="Plain")
        self.assertNotIn("asked for", plain)

    def test_update_and_convert_need_a_name_that_exists(self):
        for action in ("update", "convert_to_range"):
            self.assertIn(f"'name' is required for '{action}'", self.run_table(action=action))
        _table(self.book)
        out = self.run_table(action="update", name="Nope", style="TableStyleDark1")
        self.assertEqual(out, "excel_table error: Table 'Nope' not found in fake.xlsx. Tables: TableDOCS")
        out = self.run_table(action="update", name="TableDOCS", sheet="Second", style="TableStyleDark1")
        self.assertIn("not found in sheet 'Second' of fake.xlsx. Tables: none", out)

    def test_update_finds_the_table_on_any_sheet_whatever_the_case(self):
        table = _table(self.book, sheet="Second")
        out = self.run_table(action="update", name="tabledocs", style="TableStyleDark1")
        self.assertTrue(out.startswith("Updated table 'TableDOCS' in fake.xlsx: style=TableStyleDark1."))
        self.assertEqual(table.table_style, "TableStyleDark1")

    def test_update_finds_a_table_by_the_name_formulas_use(self):
        table = _table(self.book, name="Has Space")
        table._display = "Has_Space"
        out = self.run_table(action="update", name="Has_Space", banded_columns=True)
        self.assertIn("Updated table 'Has Space'", out)
        self.assertIn("(in formulas: Has_Space)", out)

    def test_update_with_nothing_to_change_says_what_it_could(self):
        _table(self.book)
        out = self.run_table(action="update", name="TableDOCS")
        self.assertIn("nothing to update — give at least one of new_name, style, range, banded_rows", out)

    def test_update_resizes_restyles_sets_options_and_renames(self):
        table = _table(self.book)
        out = self.run_table(action="update", name="TableDOCS", range="B8:F12", style="TableStyleDark1",
                             banded_columns=True, filter_buttons=False, new_name="Docs_2")
        self.assertTrue(out.startswith(
            "Updated table 'Docs_2' in fake.xlsx: resized to B8:F12, style=TableStyleDark1, "
            "banded_columns=true, filter_buttons=false, renamed from 'TableDOCS'.\n"
            "• Docs_2 — Data!B8:F12, 4 data rows, style TableStyleDark1; banded rows, banded columns"))
        self.assertEqual(table.name, "Docs_2")
        self.assertNotIn("⚠", out)

    def test_a_bad_new_name_stops_the_whole_update(self):
        table = _table(self.book)
        _table(self.book, "Other", addr="H8:I9", headers=("k", "v"))
        for new_name, why in (("Has Space", "cannot be a table name"), ("other", "already exists")):
            with self.subTest(new_name=new_name):
                out = self.run_table(action="update", name="TableDOCS", new_name=new_name,
                                     range="B8:F12", style="TableStyleDark1")
                self.assertIn(why, out)
                self.assertIn("Nothing was changed.", out)
        self.assertEqual((table.addr, table.table_style, table.name),
                         ("B8:F9", "TableStyleMedium2", "TableDOCS"))
        self.assertFalse([e for e in self.book.log if e[0] in ("resize", "style=", "display_name=")])

    def test_a_table_may_keep_its_own_name(self):
        _table(self.book)
        out = self.run_table(action="update", name="TableDOCS", new_name="TableDOCS", total_row=True)
        self.assertIn("total_row=true", out)
        self.assertNotIn("already exists", out)

    def test_one_refusal_does_not_hide_the_others_or_what_did_work(self):
        table = _table(self.book)
        self.book.refuse_resize = True
        out = self.run_table(action="update", name="TableDOCS", range="B9:F12", style="NoSuchStyle",
                             banded_rows=False)
        self.assertIn("Updated table 'TableDOCS' in fake.xlsx: banded_rows=false.", out)
        self.assertIn("⚠ Not applied: range B9:F12 (This range won't work because it doesn't align", out)
        self.assertIn("style NoSuchStyle (Excel does not know that style. Built-in styles are", out)
        self.assertEqual((table.addr, table.table_style), ("B8:F9", "TableStyleMedium2"))
        self.assertFalse(table.show_table_style_row_stripes)

    def test_convert_to_range_uses_the_platforms_call_and_checks_the_effect(self):
        _table(self.book)
        out = self.run_table(action="convert_to_range", name="tabledocs")
        self.assertEqual(out, "Converted table 'TableDOCS' (Data!B8:F9) in fake.xlsx back to an ordinary "
                              "range. The cells keep their values and their look; the table's name, "
                              "banding rules and filter buttons are gone.")
        self.assertIn(("Unlist", "TableDOCS"), self.book.log)
        self.assertEqual(self.book.sheets["Data"].tables, [])

    def test_convert_to_range_that_changed_nothing_says_so(self):
        _table(self.book)
        self.book.unlist_sticks = True
        out = self.run_table(action="convert_to_range", name="TableDOCS")
        self.assertIn("Excel still lists table 'TableDOCS' (Data!B8:F9)", out)


class TableToolMacTests(_PlatformCase):
    PLATFORM = "darwin"

    def test_convert_to_range_is_the_applescript_command_there(self):
        book = _Book()
        _table(book)
        out = _Host(book).do_excel_table({"action": "convert_to_range", "name": "TableDOCS"})
        self.assertTrue(out.startswith("Converted table 'TableDOCS'"))
        self.assertIn(("convert_to_range", "TableDOCS"), book.log)
        self.assertFalse([e for e in book.log if e[0] == "Unlist"])


# ── excel_freeze_panes ───────────────────────────────────────────────────────

FREEZE_8 = [("FreezePanes", False), ("SplitRow", 0), ("SplitColumn", 0), ("ScrollRow", 1),
            ("ScrollColumn", 1), ("SplitColumn", 0), ("SplitRow", 8), ("FreezePanes", True)]
UNFREEZE = FREEZE_8[:5]


class FreezeToolTests(_PlatformCase):
    def setUp(self):
        super().setUp()
        self.book = _Book()
        self.data = self.book.sheets["Data"]
        self.second = self.book.sheets["Second"]
        self.h = _Host(self.book)

    def freeze(self, **params):
        return self.h.do_excel_freeze_panes(params)

    def activations(self):
        return [name for kind, name in (e for e in self.book.log if e[0] == "activate")]

    # — reporting —

    def test_a_report_on_the_sheet_in_view_touches_nothing(self):
        self.assertEqual(self.freeze(), "Panes on [fake.xlsx]Data: not frozen.")
        self.assertEqual(self.book.log, [])

    def test_a_report_on_another_sheet_shows_it_and_puts_the_view_back(self):
        self.second.panes.put("SplitRow", 3)
        self.second.panes.put("FreezePanes", True)
        out = self.freeze(sheet="second")
        self.assertEqual(out, "Panes on [fake.xlsx]Second: frozen — top 3 rows kept in view.")
        self.assertEqual(self.activations(), ["Second", "Data"])
        self.assertIs(self.book.sheets.active, self.data)
        self.assertEqual(self.book.sets(), [])

    def test_a_freeze_made_first_and_filtered_later_reads_true(self):
        self.freeze(rows=8)
        self.data.hidden_rows = set(range(9, 150))      # a filter applied afterwards
        self.assertEqual(self.freeze(), "Panes on [fake.xlsx]Data: frozen — top 8 rows kept in view.")

    def test_a_freeze_that_swallowed_hidden_rows_is_reported_as_one(self):
        # Made behind the tool's back, over hidden rows 9-20: Excel put the
        # split after row 20 and reads it as 19 while they stay hidden.
        self.data.hidden_rows = set(range(9, 21))
        self.data.panes.put("SplitRow", 8)
        self.data.panes.put("FreezePanes", True)
        self.assertEqual(self.data.panes.get("SplitRow"), 19)
        out = self.freeze()
        self.assertTrue(out.startswith("Panes on [fake.xlsx]Data: frozen — top 8 rows kept in view.\n"))
        self.assertIn("⚠ Hidden rows from row 9 lie inside the frozen part", out)
        self.data.hidden_rows = set()                   # shown again: the truth comes out
        self.assertEqual(self.freeze(), "Panes on [fake.xlsx]Data: frozen — top 20 rows kept in view.")

    def test_swallowed_columns_are_named_by_letter(self):
        self.data.hidden_cols = {3, 4, 5}
        self.data.panes.put("SplitColumn", 2)
        self.data.panes.put("FreezePanes", True)
        out = self.freeze()
        self.assertIn("frozen — left 2 columns kept in view", out)
        self.assertIn("⚠ Hidden columns from column C lie inside", out)

    def test_the_visible_extent_is_found_by_bisection(self):
        # A filter can hide thousands of rows under a frozen header.
        self.data.hidden_rows = set(range(9, 9001))
        self.assertEqual(self.h._excel_pane_extent(self.data, 8999, "row"), 8)
        self.assertLess(self.data.probes, 20)
        self.assertEqual(self.h._excel_pane_extent(self.data, 0, "row"), 0)
        self.data.hidden_rows = set()
        self.assertEqual(self.h._excel_pane_extent(self.data, 8, "row"), 8)
        self.data.unreadable = True                     # Excel's own figure stands
        self.assertEqual(self.h._excel_pane_extent(self.data, 8, "row"), 8)

    # — freezing —

    def test_freeze_runs_the_whole_sequence_in_order(self):
        out = self.freeze(rows=8)
        self.assertEqual(out, "Froze the top 8 rows of [fake.xlsx]Data (before: not frozen).")
        self.assertEqual(self.book.sets(), FREEZE_8)
        panes = self.data.panes
        self.assertEqual((panes.frozen, panes.rows, panes.cols, panes.top_row), (True, 8, 0, 1))

    def test_rows_and_columns_together_and_counts_as_text(self):
        out = self.freeze(rows="1", columns=2.0)
        self.assertEqual(out, "Froze the top row and left 2 columns of [fake.xlsx]Data (before: not frozen).")
        self.assertEqual((self.data.panes.rows, self.data.panes.cols), (1, 2))

    def test_the_freeze_is_never_taken_from_where_the_window_is_scrolled(self):
        # Asked for 8 rows while scrolled to row 50, Excel froze rows 50-57.
        self.data.panes.put("ScrollRow", 50)
        self.freeze(rows=8)
        self.assertEqual(self.data.panes.top_row, 1)            # rows 1-8, not 50-57
        self.assertEqual(self.data.panes.get("ScrollRow"), 50)  # and the user is back where they were

    def test_a_freeze_on_another_sheet_leaves_the_view_as_it_was(self):
        self.freeze(sheet="Second", rows=1)
        self.assertEqual(self.activations(), ["Second", "Data"])
        self.assertTrue(self.second.panes.frozen)
        self.assertFalse(self.data.panes.frozen)

    def test_a_workbook_brought_forward_by_the_way_is_put_back(self):
        # Showing a sheet of a workbook that is not in front sometimes brings
        # the workbook forward and sometimes does not: both are handled.
        other = _Book("other.xlsx", app=self.book.app)
        self.book.app.books.active = other
        self.book.activating_fronts = True
        self.freeze(sheet="Second", rows=1)
        self.assertIs(self.book.app.books.active, other)
        self.assertIn(("book.activate", "other.xlsx"), other.log)
        self.assertFalse(other.sheets.active.panes.frozen)      # the bystander's own panes: untouched
        self.assertEqual(other.sets(), [])

    def test_scrolling_is_put_back_only_where_the_user_had_scrolled(self):
        self.freeze(rows=1, columns=2)      # ScrollColumn now reads 3: the scrollable part's first column
        self.freeze(rows=8)                 # no frozen columns any more
        # Put back as a bare number, that 3 scrolled columns A and B out of view.
        self.assertEqual(self.data.panes.top_col, 1)
        self.data.panes.put("ScrollRow", 300)
        self.freeze(rows=3)
        self.assertEqual(self.data.panes.get("ScrollRow"), 300)

    def test_asking_for_what_is_already_so_touches_nothing(self):
        self.freeze(rows=8)
        self.book.log.clear()
        out = self.freeze(rows=8)
        self.assertEqual(out, "Panes on [fake.xlsx]Data are already frozen that way "
                              "(frozen — top 8 rows kept in view) — nothing to do.")
        self.assertEqual(self.book.sets(), [])

    def test_a_filtered_sheet_that_is_already_frozen_right_is_not_refused(self):
        # The everyday case: frozen first, filtered later. Row 9 is hidden,
        # which would block a NEW freeze of 8 — but nothing needs doing.
        self.freeze(rows=8)
        self.data.hidden_rows = set(range(9, 150))
        self.book.log.clear()
        self.assertIn("already frozen that way", self.freeze(rows=8))
        self.assertEqual(self.book.sets(), [])

    def test_a_freeze_that_only_reads_right_is_made_again(self):
        # Frozen while scrolled: the same flag and the same split as a proper
        # one, and rows 50-57 in view. Only the frozen pane's own position
        # tells them apart.
        self.data.panes.put("ScrollRow", 50)
        self.data.panes.put("SplitRow", 8)
        self.data.panes.put("FreezePanes", True)
        out = self.freeze(rows=8)
        self.assertTrue(out.startswith("Froze the top 8 rows"))
        self.assertEqual(self.data.panes.top_row, 1)

    # — the hidden-row rule —

    def test_a_freeze_over_a_hidden_row_is_refused_untouched(self):
        self.data.hidden_rows = set(range(9, 21))
        out = self.freeze(rows=8)
        self.assertIn("row 9, directly below the 8 rows to freeze, is hidden (filtered out?)", out)
        self.assertIn("show them (clear the filter) first, then freeze", out)
        self.assertIn("Nothing was changed on [fake.xlsx]Data.", out)
        self.assertEqual(self.book.sets(), [])
        self.assertFalse(self.data.panes.frozen)

    def test_one_row_and_columns_are_worded_for_what_they_are(self):
        self.data.hidden_rows = {2}
        self.assertIn("row 2, directly below the row to freeze,", self.freeze(rows=1))
        self.data.hidden_rows = set()
        self.data.hidden_cols = {3, 4, 5}
        out = self.freeze(columns=2)
        self.assertIn("column C, directly right of the 2 columns to freeze, is hidden.", out)
        self.assertNotIn("filter", out)         # a filter hides rows, never columns
        self.assertEqual(self.book.sets(), [])

    def test_hidden_rows_elsewhere_do_not_block(self):
        self.data.hidden_rows = {3, 4, 30}      # inside the frozen part, and far below it
        self.assertTrue(self.freeze(rows=8).startswith("Froze the top 8 rows"))
        self.assertEqual(self.data.panes.rows, 8)

    def test_unfreezing_is_never_blocked_by_hidden_rows(self):
        self.freeze(rows=8)
        self.data.hidden_rows = set(range(9, 21))
        self.assertTrue(self.freeze(rows=0).startswith("Unfroze the panes"))

    def test_visibility_that_cannot_be_read_falls_back_on_the_read_back(self):
        # The rule cannot fire, Excel swallows rows 9-20 — and the read-back
        # (19, where 8 was asked) catches it and the panes are put back.
        self.data.hidden_rows = set(range(9, 21))
        self.data.unreadable = True
        out = self.freeze(rows=8)
        self.assertIn("could not freeze the top 8 rows of [fake.xlsx]Data", out)
        self.assertIn("The panes were put back as they were (not frozen).", out)
        self.assertFalse(self.data.panes.frozen)
        self.assertEqual(self.data.panes.rows, 0)

    # — unfreezing —

    def test_unfreeze_clears_the_split_as_well(self):
        self.freeze(rows=8)
        self.book.log.clear()
        out = self.freeze(rows=0, columns=0)
        self.assertEqual(out, "Unfroze the panes of [fake.xlsx]Data "
                              "(before: frozen — top 8 rows kept in view).")
        self.assertEqual(self.book.sets(), UNFREEZE)
        self.assertEqual((self.data.panes.frozen, self.data.panes.rows), (False, 0))

    def test_unfreeze_removes_an_ordinary_split_too(self):
        self.data.panes.put("SplitRow", 5)
        out = self.freeze(rows=0)
        self.assertIn("(before: split but not frozen (below row 5))", out)
        self.assertEqual(self.data.panes.rows, 0)

    def test_nothing_frozen_nothing_done(self):
        self.assertEqual(self.freeze(rows=0, columns=0),
                         "Panes on [fake.xlsx]Data were not frozen — nothing to do.")
        self.assertEqual(self.book.sets(), [])

    # — refusals and failures —

    def test_a_bad_count_never_reaches_excel(self):
        class NoExcel(ExcelMixin):
            def _excel_target(self, params):
                raise AssertionError("reached Excel")
        for value, why in ((-1, "cannot be negative"), (2.5, "whole number"), ("eight", "whole number"),
                           (1_048_576, "must be below 1,048,576")):
            with self.subTest(value=value):
                out = NoExcel().do_excel_freeze_panes({"rows": value})
                self.assertTrue(out.startswith("excel_freeze_panes error: 'rows'"))
                self.assertIn(why, out)
        self.assertIn("'columns' must be below 16,384",
                      NoExcel().do_excel_freeze_panes({"columns": 16_384}))

    def test_page_layout_view_is_refused_before_the_old_panes_are_lost(self):
        self.freeze(rows=3)
        self.data.panes.view = 3
        self.book.log.clear()
        out = self.freeze(rows=8)
        self.assertIn("the sheet is in Page Layout view, which has no panes", out)
        self.assertEqual(self.book.sets(), [])
        self.assertEqual(self.data.panes.rows, 3)
        self.assertIn("none — the sheet is in Page Layout view", self.freeze())

    def test_the_silent_substitute_is_caught_and_the_panes_put_back(self):
        self.freeze(rows=1, columns=2)
        out = self.freeze(rows=1_048_575)
        self.assertIn("could not freeze the top 1048575 rows of [fake.xlsx]Data: Excel reports them "
                      "frozen — top 16 rows and left 10 columns kept in view.", out)
        self.assertIn("The panes were put back as they were "
                      "(frozen — top row and left 2 columns kept in view).", out)
        panes = self.data.panes
        self.assertEqual((panes.frozen, panes.rows, panes.cols, panes.top_row), (True, 1, 2, 1))

    def test_a_write_that_raises_half_way_puts_the_panes_back(self):
        self.freeze(rows=3)
        self.book.fail_on = ("SplitRow", 8)
        out = self.freeze(rows=8)
        self.assertIn("could not freeze the top 8 rows of [fake.xlsx]Data: "
                      "Unable to set the SplitRow property of the Window class.", out)
        self.assertIn("put back as they were (frozen — top 3 rows kept in view)", out)
        self.assertEqual((self.data.panes.frozen, self.data.panes.rows), (True, 3))

    def test_panes_that_cannot_be_put_back_are_not_claimed_to_be(self):
        self.freeze(rows=3)
        self.book.fail_on = ("FreezePanes", True)       # the freeze AND the put-back both fail
        out = self.freeze(rows=8)
        self.assertIn("⚠ The panes could NOT be put back as they were", out)
        self.assertNotIn("were put back", out)

    def test_a_frozen_part_larger_than_the_window_is_done_and_warned_about(self):
        out = self.freeze(rows=40)          # the window shows 32
        self.assertTrue(out.startswith("Froze the top 40 rows of [fake.xlsx]Data"))
        self.assertIn("⚠ Excel reads this split one short", out)
        self.assertEqual(self.data.panes.rows, 40)
        self.assertNotIn("⚠", self.freeze(sheet="Second", rows=8))

    def test_a_hidden_sheet_cannot_be_shown_and_says_so(self):
        self.second.visible = False
        out = self.freeze(sheet="Second", rows=1)
        self.assertIn("could not show sheet 'Second' in fake.xlsx's window (is it hidden?)", out)
        self.assertEqual(self.book.sets(), [])
        self.assertFalse(self.data.panes.frozen)    # NOT frozen on the sheet that was showing instead

    def test_a_missing_sheet_is_the_usual_friendly_error(self):
        out = self.freeze(sheet="Nope", rows=1)
        self.assertEqual(out, "excel_freeze_panes error: Sheet 'Nope' not found in fake.xlsx. "
                              "Sheets: Data, Second")

    def test_the_window_is_the_workbooks_own_never_the_applications(self):
        # The agent is attached to the user's Excel: a freeze through the
        # application's active window would land on whatever is in front.
        import inspect
        source = inspect.getsource(ExcelMixin._excel_pane_window)
        windows, _, mac = source.rpartition('if sys.platform == "darwin":')
        self.assertIn("book.api.Windows(1)", source)
        self.assertNotIn("ActiveWindow", source.split('"""')[-1])
        self.assertTrue(windows and mac)
        other = _Book("other.xlsx", app=self.book.app)
        self.book.app.books.active = other          # the user's workbook is in front
        self.freeze(rows=8)
        self.assertTrue(self.data.panes.frozen)
        self.assertEqual(other.sets(), [])
        self.assertIs(self.book.app.books.active, other)


class FreezeToolMacTests(_PlatformCase):
    """The appscript shape: properties with .get() / .set(), reached through
    the application's active window. NOT yet run against a real Excel on a Mac
    — these pin the shape and the guards, not the platform."""
    PLATFORM = "darwin"

    def setUp(self):
        super().setUp()
        self.book = _Book()
        self.data = self.book.sheets["Data"]
        self.h = _Host(self.book)

    def test_freeze_goes_through_the_get_set_references_in_the_same_order(self):
        out = self.h.do_excel_freeze_panes({"rows": 8})
        self.assertEqual(out, "Froze the top 8 rows of [fake.xlsx]Data (before: not frozen).")
        self.assertEqual(self.book.sets(), FREEZE_8)
        self.assertEqual((self.data.panes.frozen, self.data.panes.rows), (True, 8))

    def test_the_workbook_is_brought_forward_first_and_the_view_restored(self):
        other = _Book("other.xlsx", app=self.book.app)
        self.book.app.books.active = other
        self.h.do_excel_freeze_panes({"sheet": "Second", "rows": 1})
        self.assertEqual(self.book.log[0], ("book.activate", "fake.xlsx"))
        self.assertTrue(self.book.sheets["Second"].panes.frozen)
        self.assertIs(self.book.sheets.active, self.data)
        self.assertIs(self.book.app.books.active, other)
        self.assertEqual(other.sets(), [])

    def test_a_workbook_that_did_not_come_forward_is_never_frozen_blind(self):
        other = _Book("other.xlsx", app=self.book.app)
        self.book.app.books.active = other
        self.book.activate = lambda: None           # the activation is ignored
        out = self.h.do_excel_freeze_panes({"rows": 8})
        self.assertIn("could not bring sheet 'Data' of fake.xlsx to the front", out)
        self.assertEqual(self.book.sets(), [])
        self.assertEqual(other.sets(), [])
        self.assertFalse(other.sheets.active.panes.frozen)

    def test_page_layout_is_read_from_the_keyword(self):
        self.data.panes.view = 3
        out = self.h.do_excel_freeze_panes({"rows": 8})
        self.assertIn("Page Layout view", out)
        self.assertEqual(self.book.sets(), [])

    def test_already_so_cannot_be_told_there_so_the_freeze_is_made_again(self):
        # The frozen pane's own position is out of reach, and without it a
        # freeze made while scrolled reads like a proper one.
        self.h.do_excel_freeze_panes({"rows": 8})
        self.book.log.clear()
        out = self.h.do_excel_freeze_panes({"rows": 8})
        self.assertTrue(out.startswith("Froze the top 8 rows"))
        self.assertEqual(self.book.sets(), FREEZE_8)
        self.assertIsNone(self.h._excel_pane_home(self.book.window, 8, 0))


if __name__ == "__main__":
    unittest.main()
