"""Characterization tests for ExcelMixin's pure helpers.

Only the no-IO staticmethods are covered — the live xlwings surface needs a
running Excel and is exercised by hand (see CLAUDE_MYAGENT.md). Tests run on
the bare class: no Tk, no xlwings import required (the mixin degrades to
xw=None gracefully).
"""

import datetime
import decimal
import unittest

from myagent.excel_mixin import ExcelMixin


class TestColLetter(unittest.TestCase):
    def test_single_letters(self):
        self.assertEqual(ExcelMixin._excel_col_letter(1), "A")
        self.assertEqual(ExcelMixin._excel_col_letter(26), "Z")

    def test_double_letters(self):
        self.assertEqual(ExcelMixin._excel_col_letter(27), "AA")
        self.assertEqual(ExcelMixin._excel_col_letter(52), "AZ")
        self.assertEqual(ExcelMixin._excel_col_letter(53), "BA")
        self.assertEqual(ExcelMixin._excel_col_letter(702), "ZZ")

    def test_triple_letters(self):
        self.assertEqual(ExcelMixin._excel_col_letter(703), "AAA")
        # XFD is Excel's last column (16384)
        self.assertEqual(ExcelMixin._excel_col_letter(16384), "XFD")


class TestHexToRgb(unittest.TestCase):
    def test_with_hash(self):
        self.assertEqual(ExcelMixin._excel_hex_to_rgb("#FF8000"), (255, 128, 0))

    def test_without_hash_lowercase(self):
        self.assertEqual(ExcelMixin._excel_hex_to_rgb("ff8000"), (255, 128, 0))

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            ExcelMixin._excel_hex_to_rgb("#FFF")
        with self.assertRaises(ValueError):
            ExcelMixin._excel_hex_to_rgb("red")


class TestCellStr(unittest.TestCase):
    def test_none_is_empty(self):
        self.assertEqual(ExcelMixin._excel_cell_str(None), "")

    def test_booleans(self):
        self.assertEqual(ExcelMixin._excel_cell_str(True), "TRUE")
        self.assertEqual(ExcelMixin._excel_cell_str(False), "FALSE")

    def test_integral_float_drops_point(self):
        # COM returns every number as float; 42.0 must render as 42
        self.assertEqual(ExcelMixin._excel_cell_str(42.0), "42")
        self.assertEqual(ExcelMixin._excel_cell_str(-3.0), "-3")

    def test_real_float_kept(self):
        self.assertEqual(ExcelMixin._excel_cell_str(3.5), "3.5")

    def test_decimal_from_currency_format(self):
        # COM returns currency-formatted cells as VT_CY → decimal.Decimal
        # with trailing zeros (found live 2026-08-01); must render like float
        self.assertEqual(
            ExcelMixin._excel_cell_str(decimal.Decimal("7.5000")), "7.5")
        self.assertEqual(
            ExcelMixin._excel_cell_str(decimal.Decimal("42.0000")), "42")

    def test_midnight_datetime_is_bare_date(self):
        self.assertEqual(
            ExcelMixin._excel_cell_str(datetime.datetime(2026, 8, 1)),
            "2026-08-01")

    def test_datetime_with_time(self):
        self.assertEqual(
            ExcelMixin._excel_cell_str(datetime.datetime(2026, 8, 1, 9, 30, 5)),
            "2026-08-01 09:30:05")

    def test_date_object(self):
        self.assertEqual(
            ExcelMixin._excel_cell_str(datetime.date(2026, 8, 1)), "2026-08-01")

    def test_tabs_newlines_flattened(self):
        # TSV rows must stay rectangular
        self.assertEqual(ExcelMixin._excel_cell_str("a\tb\nc\r"), "a b c ")


class TestAs2d(unittest.TestCase):
    def test_scalar(self):
        self.assertEqual(ExcelMixin._excel_as_2d(5.0), [[5.0]])
        self.assertEqual(ExcelMixin._excel_as_2d(None), [[None]])

    def test_flat_sequence_is_one_row(self):
        self.assertEqual(ExcelMixin._excel_as_2d([1, 2, 3]), [[1, 2, 3]])

    def test_2d_tuples_become_lists(self):
        self.assertEqual(
            ExcelMixin._excel_as_2d(((1, 2), (3, 4))), [[1, 2], [3, 4]])

    def test_empty_sequence(self):
        self.assertEqual(ExcelMixin._excel_as_2d([]), [[None]])


class TestValuesMatrix(unittest.TestCase):
    def test_scalar(self):
        self.assertEqual(ExcelMixin._excel_values_matrix("x"), [["x"]])

    def test_flat_list_is_one_row(self):
        self.assertEqual(
            ExcelMixin._excel_values_matrix(["a", "b"]), [["a", "b"]])

    def test_ragged_rows_padded_with_none(self):
        self.assertEqual(
            ExcelMixin._excel_values_matrix([["a", "b"], ["c"]]),
            [["a", "b"], ["c", None]])

    def test_empty_string_becomes_none(self):
        # '' means "empty cell", which COM writes as a true blank
        self.assertEqual(
            ExcelMixin._excel_values_matrix([["a", ""], ["", "d"]]),
            [["a", None], [None, "d"]])

    def test_empty_list_raises(self):
        with self.assertRaises(ValueError):
            ExcelMixin._excel_values_matrix([])
        with self.assertRaises(ValueError):
            ExcelMixin._excel_values_matrix([[]])


class TestMatrixTsv(unittest.TestCase):
    def test_headers_use_real_offsets(self):
        # A matrix whose top-left is C10 must label columns C,D and rows 10,11
        out = ExcelMixin._excel_matrix_tsv(
            [[1.0, 2.0], [3.0, None]], first_row=10, first_col=3)
        lines = out.split("\n")
        self.assertEqual(lines[0], "\tC\tD")
        self.assertEqual(lines[1], "10\t1\t2")
        self.assertEqual(lines[2], "11\t3\t")


class TestResolveSheetName(unittest.TestCase):
    NAMES = ["Sheet1", "Data Sheet", "Summary"]

    def test_exact_match(self):
        self.assertEqual(
            ExcelMixin._excel_resolve_sheet_name(self.NAMES, "Summary"),
            "Summary")

    def test_case_insensitive_returns_the_real_name(self):
        # Windows COM's lookup is case-insensitive; macOS's is not, so the
        # helper normalizes both platforms onto the real cased name.
        self.assertEqual(
            ExcelMixin._excel_resolve_sheet_name(self.NAMES, "summary"),
            "Summary")
        self.assertEqual(
            ExcelMixin._excel_resolve_sheet_name(self.NAMES, "DATA sheet"),
            "Data Sheet")

    def test_surrounding_whitespace_tolerated(self):
        self.assertEqual(
            ExcelMixin._excel_resolve_sheet_name(self.NAMES, "  Sheet1 "),
            "Sheet1")

    def test_missing_returns_none(self):
        # None is what makes the friendly "not found" error fire, instead of
        # a lazy macOS reference blowing up later with a raw OSERROR -1728.
        self.assertIsNone(
            ExcelMixin._excel_resolve_sheet_name(self.NAMES, "NoSuch"))

    def test_no_partial_match(self):
        self.assertIsNone(
            ExcelMixin._excel_resolve_sheet_name(self.NAMES, "Sheet"))


class TestWriteDropped(unittest.TestCase):
    def test_all_expected_all_empty_actual_is_a_drop(self):
        self.assertTrue(ExcelMixin._excel_write_dropped(
            [["Item", "Qty"], ["Widget", 12]], [[None, None], [None, None]]))

    def test_values_landed_is_not_a_drop(self):
        self.assertFalse(ExcelMixin._excel_write_dropped(
            [["Item", "Qty"]], [["Item", 12]]))

    def test_partial_read_back_is_not_a_drop(self):
        # One surviving cell means Excel is honouring writes; whatever else
        # happened isn't the silent-discard failure mode.
        self.assertFalse(ExcelMixin._excel_write_dropped(
            [["a", "b"]], [["a", None]]))

    def test_intentionally_blank_write_never_warns(self):
        # A formula returning "" (or a deliberately cleared cell) must not be
        # reported as a dropped write — hence the all-expected-non-empty gate.
        self.assertFalse(ExcelMixin._excel_write_dropped(
            [["a", None]], [[None, None]]))
        self.assertFalse(ExcelMixin._excel_write_dropped(
            [["", ""]], [[None, None]]))

    def test_empty_inputs_never_warn(self):
        self.assertFalse(ExcelMixin._excel_write_dropped([], []))
        self.assertFalse(ExcelMixin._excel_write_dropped([["a"]], []))

    def test_zero_is_not_treated_as_empty(self):
        # 0 and False are falsy but are real written values.
        self.assertTrue(ExcelMixin._excel_write_dropped([[0]], [[None]]))
        self.assertFalse(ExcelMixin._excel_write_dropped([[0]], [[0]]))


class TestOpenKwargs(unittest.TestCase):
    def test_no_params_is_a_bare_open(self):
        # An unprotected open must stay byte-identical to open(path).
        self.assertEqual(ExcelMixin._excel_open_kwargs({}), {})
        self.assertEqual(
            ExcelMixin._excel_open_kwargs({"path": "x.xlsx"}), {})

    def test_open_password_only(self):
        self.assertEqual(
            ExcelMixin._excel_open_kwargs({"password": "s3cret"}),
            {"password": "s3cret"})

    def test_both_passwords_are_independent_locks(self):
        # A workbook can be encrypted AND write-reserved; they are separate
        # passwords and both must reach xlwings.
        self.assertEqual(
            ExcelMixin._excel_open_kwargs(
                {"password": "open-pw", "write_res_password": "write-pw"}),
            {"password": "open-pw", "write_res_password": "write-pw"})

    def test_write_res_password_alone(self):
        self.assertEqual(
            ExcelMixin._excel_open_kwargs({"write_res_password": "w"}),
            {"write_res_password": "w"})

    def test_blank_and_whitespace_are_omitted_not_sent_empty(self):
        # Sending password="" is not the same as omitting it.
        self.assertEqual(
            ExcelMixin._excel_open_kwargs(
                {"password": "", "write_res_password": "   "}), {})
        self.assertEqual(
            ExcelMixin._excel_open_kwargs({"password": None}), {})

    def test_passwords_are_stripped(self):
        self.assertEqual(
            ExcelMixin._excel_open_kwargs({"password": "  pw  "}),
            {"password": "pw"})

    def test_ignore_read_only_recommended_only_when_true(self):
        self.assertEqual(
            ExcelMixin._excel_open_kwargs({"ignore_read_only_recommended": True}),
            {"ignore_read_only_recommended": True})
        # False must be omitted, not sent as False
        self.assertEqual(
            ExcelMixin._excel_open_kwargs({"ignore_read_only_recommended": False}),
            {})


class _WinApi:
    """Windows COM shape: a plain .ReadOnly attribute."""

    def __init__(self, value):
        self.ReadOnly = value


class _MacProp:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class _MacApi:
    """macOS appscript shape: .read_only.get(). Touching .ReadOnly raises,
    which is what the probe chain has to fall through."""

    def __init__(self, value):
        self.read_only = _MacProp(value)


class _BookWithApi:
    def __init__(self, api):
        self.api = api


class TestIsReadOnly(unittest.TestCase):
    def test_windows_shape(self):
        self.assertTrue(ExcelMixin._excel_is_read_only(
            _BookWithApi(_WinApi(True))))
        self.assertFalse(ExcelMixin._excel_is_read_only(
            _BookWithApi(_WinApi(False))))

    def test_macos_shape_falls_through_the_windows_probe(self):
        # _MacApi has no .ReadOnly, so the first probe raises AttributeError
        # and the chain must continue rather than give up.
        self.assertTrue(ExcelMixin._excel_is_read_only(
            _BookWithApi(_MacApi(True))))
        self.assertFalse(ExcelMixin._excel_is_read_only(
            _BookWithApi(_MacApi(False))))

    def test_unknown_shape_defaults_to_not_read_only(self):
        # Never invent a scary warning from a probe that couldn't answer.
        self.assertFalse(ExcelMixin._excel_is_read_only(_BookWithApi(object())))


class _FakeRange:
    """Stand-in for an xlwings Range over a shared cell grid, so resize()
    views the same cells the write landed in (or didn't)."""

    def __init__(self, sheet, row, column, rows=1, cols=1):
        self.sheet = sheet
        self.row, self.column = row, column
        self._rows, self._cols = rows, cols

    def resize(self, rows, cols):
        return _FakeRange(self.sheet, self.row, self.column, rows, cols)

    @property
    def address(self):
        return f"${self.column}${self.row}"

    @property
    def value(self):
        return [[self.sheet.cells.get((self.row + r, self.column + c))
                 for c in range(self._cols)] for r in range(self._rows)]

    @value.setter
    def value(self, matrix):
        if self.sheet.drops:      # the wedged-Excel mode: accepted, discarded
            return
        for r, row in enumerate(matrix):
            for c, v in enumerate(row):
                self.sheet.cells[(self.row + r, self.column + c)] = v

    def options(self, **kwargs):
        return self


class _FakeSheet:
    def __init__(self, drops=False):
        self.name = "Sheet1"
        self.drops = drops
        self.cells = {}

    def range(self, addr):
        return _FakeRange(self, 1, 1)


class _FakeBook:
    name = "fake.xlsx"


class _WriteHost(ExcelMixin):
    def __init__(self, drops):
        self._sheet = _FakeSheet(drops)

    def _excel_com_init(self):
        pass

    def _excel_target(self, params):
        return _FakeBook(), self._sheet


class TestWriteVerificationWiring(unittest.TestCase):
    """The pure helper is covered above; these check do_excel_write actually
    consults it, on BOTH the echo path and the large-write probe path."""

    SMALL = [["Item", "Qty"], ["Widget", 12]]
    LARGE = [[f"r{r}c{c}" for c in range(10)] for r in range(30)]  # 300 cells

    def test_small_write_warns_when_dropped(self):
        out = _WriteHost(drops=True).do_excel_write(
            {"start_cell": "A1", "values": self.SMALL})
        self.assertIn("VERIFICATION FAILED", out)

    def test_small_write_silent_when_it_lands(self):
        out = _WriteHost(drops=False).do_excel_write(
            {"start_cell": "A1", "values": self.SMALL})
        self.assertNotIn("VERIFICATION FAILED", out)
        self.assertIn("Current values", out)

    def test_large_write_warns_when_dropped(self):
        # The regression this closes: >200 cells skip the echo, so before the
        # probe a dropping Excel produced a bare "Wrote 30x10 cells."
        out = _WriteHost(drops=True).do_excel_write(
            {"start_cell": "A1", "values": self.LARGE})
        self.assertIn("VERIFICATION FAILED", out)

    def test_large_write_silent_when_it_lands(self):
        out = _WriteHost(drops=False).do_excel_write(
            {"start_cell": "A1", "values": self.LARGE})
        self.assertNotIn("VERIFICATION FAILED", out)
        # still no full echo for large writes
        self.assertNotIn("Current values", out)


if __name__ == "__main__":
    unittest.main()
