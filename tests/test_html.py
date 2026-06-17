"""Characterization test: helpers.extract_text_from_html.

Locks: script/style/noscript content is skipped; block-level END tags
(p, br, div, h1-h6, li, tr) emit a newline; entities are unescaped; result is
.strip()ed. Values are ACTUAL captured outputs."""
import unittest

from myagent.helpers import extract_text_from_html

CASES = [
    ("<p>Hello</p><p>World</p>", "Hello\nWorld"),
    ("<div>a</div><div>b</div>", "a\nb"),
    ("<script>var x=1;</script>Visible<style>.c{}</style>", "Visible"),
    ("Plain &amp; simple &lt;tag&gt;", "Plain & simple <tag>"),
    ("<h1>Title</h1><li>one</li><li>two</li>", "Title\none\ntwo"),
    ("<b>bold</b> and <i>italic</i>", "bold and italic"),
    ("", ""),
    ("no tags here", "no tags here"),
]


class TestExtractTextFromHtml(unittest.TestCase):
    def test_cases(self):
        for src, expected in CASES:
            with self.subTest(src=src):
                self.assertEqual(extract_text_from_html(src), expected)


if __name__ == "__main__":
    unittest.main()
