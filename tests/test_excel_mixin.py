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


if __name__ == "__main__":
    unittest.main()
