"""Characterization test: DocumentMixin._parse_pages_range (static).

Locks: 1-indexed input -> 0-indexed output; out-of-range silently dropped;
reversed ranges (5-3) yield []; duplicates removed preserving order;
None/empty/garbage -> []. Values are ACTUAL captured outputs."""
import unittest

from myagent.document_mixin import DocumentMixin

# (spec, total) -> expected
CASES = [
    (("1-5", 10), [0, 1, 2, 3, 4]),
    (("3", 10), [2]),
    (("1,3,5-7", 10), [0, 2, 4, 5, 6]),
    (("8-12", 10), [7, 8, 9]),
    (("0", 10), []),
    (("", 10), []),
    (("abc", 10), []),
    (("5-3", 10), []),
    (("2,2,2", 10), [1]),
    (("1-3,2-4", 10), [0, 1, 2, 3]),
    ((None, 10), []),
    (("1-100", 3), [0, 1, 2]),
]


class TestParsePagesRange(unittest.TestCase):
    def test_cases(self):
        for (spec, total), expected in CASES:
            with self.subTest(spec=spec, total=total):
                self.assertEqual(
                    DocumentMixin._parse_pages_range(spec, total), expected)


if __name__ == "__main__":
    unittest.main()
