"""Regression tests for the 2026-09-28 review fixes in the tool mixins.

- write_file / edit_file never leave the user's file empty: the write goes to
  a temp file first, and non-string content is refused up front;
- file tools expand ~, keep a CRLF file CRLF, count overlapping matches, stop
  the read_file window at the size cap on a whole line (and say where), and
  flag a file that is not UTF-8;
- read_document reads an owner-password-only PDF and names a user-password
  one plainly;
- Excel: an exact workbook name wins and another extension never matches,
  excel_open refuses a same-named file from another folder, excel_close needs
  a name when several are open, a formula write is not a "dropped write";
- csv_search survives rows with extra fields; _compress_image handles LA and
  16-bit images and applies EXIF rotation; open_application's args reach the
  curated apps quoted; mouse_scroll moves before it scrolls; downloads never
  replace a same-named file; the LaTeX pass leaves \\left / \\inf alone.
"""

import csv
import inspect
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from myagent import desktop_mixin
from myagent.browser_mixin import BrowserMixin
from myagent.chat_mixin import ChatMixin
from myagent.constants import IS_WINDOWS
from myagent.desktop_mixin import DesktopMixin
from myagent.document_mixin import DocumentMixin
from myagent.excel_mixin import ExcelMixin
from myagent.file_mixin import FILE_MAX_READ_CHARS, FileMixin
from myagent.safety_mixin import SafetyMixin
from tests._util import stub


class _TempCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def path(self, name):
        return os.path.join(self.dir, name)

    def write_bytes(self, name, data):
        with open(self.path(name), "wb") as f:
            f.write(data)
        return self.path(name)

    def read_bytes(self, name):
        with open(self.path(name), "rb") as f:
            return f.read()


class FileWriteSafetyTests(_TempCase):

    def host(self, *read):
        h = stub(FileMixin)
        for p in read:
            h._file_reads().add(h._file_key(p))
        return h

    def test_non_string_content_is_refused_and_the_file_kept(self):
        p = self.write_bytes("config.json", b'{"keep": true}')
        out = self.host(p).do_write_file({"path": p, "content": {"a": 1}})
        self.assertIn("must be a string", out)
        self.assertEqual(self.read_bytes("config.json"), b'{"keep": true}')

    def test_a_write_that_fails_half_way_leaves_the_old_file(self):
        p = self.write_bytes("notes.txt", b"original")
        out = self.host(p).do_write_file({"path": p, "content": "split emoji \ud83d"})
        self.assertIn("write_file error", out)
        self.assertEqual(self.read_bytes("notes.txt"), b"original")
        self.assertEqual([n for n in os.listdir(self.dir) if n.endswith(".tmp")], [])

    def test_an_edit_that_cannot_be_written_leaves_the_old_file(self):
        p = self.write_bytes("notes.txt", b"hello world")
        out = self.host(p).do_edit_file({"path": p, "old_string": "world",
                                         "new_string": "\ud83d"})
        self.assertIn("edit_file error", out)
        self.assertEqual(self.read_bytes("notes.txt"), b"hello world")

    def test_tilde_is_expanded(self):
        home = os.path.join(self.dir, "home")
        os.makedirs(home)
        with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
            out = stub(FileMixin).do_write_file({"path": "~/report.md", "content": "hi"})
        self.assertIn("Created", out)
        self.assertTrue(os.path.isfile(os.path.join(home, "report.md")))
        self.assertFalse(os.path.exists(os.path.join(os.getcwd(), "~")))


class FileEditSemanticsTests(unittest.TestCase):

    def test_a_crlf_file_stays_crlf(self):
        out, _count, err = FileMixin._file_apply_edit(
            "import os\r\nprint(1)\r\n", "import os", "import os\nimport sys")
        self.assertIsNone(err)
        self.assertEqual(out, "import os\r\nimport sys\r\nprint(1)\r\n")

    def test_overlapping_matches_are_not_unique(self):
        _out, _count, err = FileMixin._file_apply_edit("end\nend\nend\n", "end\nend", "X")
        self.assertIn("appears 2 times", err)

    def test_the_read_window_stops_at_the_cap_on_a_whole_line(self):
        text = "\n".join("x" * 400 for _ in range(1000))
        body, total, first, last = FileMixin._file_numbered(text)
        self.assertEqual((total, first), (1000, 1))
        self.assertLess(last, 1000)
        self.assertLessEqual(len(body), FILE_MAX_READ_CHARS)
        self.assertEqual(body.splitlines()[-1].split("\t")[0].strip(), str(last))

    def test_the_grep_glob_matches_paths_too(self):
        base = os.path.join("proj")
        fp = os.path.join("proj", "src", "pkg", "mod.py")
        self.assertTrue(FileMixin._file_glob_matches(fp, base, "*.py"))
        self.assertTrue(FileMixin._file_glob_matches(fp, base, "**/*.py"))
        self.assertTrue(FileMixin._file_glob_matches(fp, base, "src/**/*.py"))
        self.assertFalse(FileMixin._file_glob_matches(fp, base, "docs/**/*.py"))


class ReadFileTests(_TempCase):

    def test_a_non_utf8_file_is_flagged(self):
        p = self.write_bytes("ansi.txt", b"caf\xe9 na\xefve\n")
        out = stub(FileMixin).do_read_file({"path": p})
        self.assertIn("not valid UTF-8", out)

    def test_the_cap_note_points_past_the_last_line_shown(self):
        p = self.write_bytes("big.txt", ("\n".join("y" * 400 for _ in range(1000))).encode())
        out = stub(FileMixin).do_read_file({"path": p})
        header = out.splitlines()[0]
        last = int(header.split("lines 1-")[1].split(" of")[0])
        self.assertLess(last, 1000)
        self.assertIn(f"re-read with offset={last + 1}", out)

    def test_the_read_gate_is_reset_by_each_run(self):
        self.assertIn("self._file_read_paths = set()",
                      inspect.getsource(SafetyMixin._start_agent))


def _make_pdfs(folder):
    """A one-page PDF with real text, plus owner-only and user-password
    copies (as the reviewers' probe built them)."""
    import pypdf
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    w = PdfWriter()
    page = w.add_blank_page(width=300, height=200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): w._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 18 Tf 20 100 Td (Statement balance 123.45) Tj ET")
    page[NameObject("/Contents")] = w._add_object(stream)
    plain = os.path.join(folder, "plain.pdf")
    with open(plain, "wb") as f:
        w.write(f)
    out = {}
    for name, user_pw in (("owner_only.pdf", ""), ("user_pw.pdf", "open-sesame")):
        w2 = PdfWriter()
        for p in pypdf.PdfReader(plain).pages:
            w2.add_page(p)
        w2.encrypt(user_password=user_pw, owner_password="owner-secret", algorithm="AES-128")
        out[name] = os.path.join(folder, name)
        with open(out[name], "wb") as f:
            w2.write(f)
    return out


class ReadDocumentTests(_TempCase):

    def setUp(self):
        super().setUp()
        try:
            self.pdfs = _make_pdfs(self.dir)
        except Exception as e:           # pypdf / its AES backend missing
            self.skipTest(f"cannot build encrypted PDFs here: {e}")

    def read(self, name):
        return json.loads(stub(DocumentMixin).do_read_document({"path": self.pdfs[name]}))

    def test_an_owner_password_only_pdf_is_read(self):
        self.assertIn("Statement balance 123.45", self.read("owner_only.pdf")["text"])

    def test_a_user_password_pdf_gets_the_plain_explanation(self):
        text = self.read("user_pw.pdf")["text"]
        self.assertIn("user password", text)
        self.assertNotIn("FileNotDecryptedError", text)


class _Book:
    def __init__(self, name, fullname=None):
        self.name, self.fullname = name, fullname or name
        self.closed = self.activated = False

    def close(self):
        self.closed = True

    def activate(self):
        self.activated = True

    def save(self, *args):
        pass


class _Books(list):
    @property
    def count(self):
        return len(self)

    @property
    def active(self):
        return self[0] if self else None

    def open(self, path, **kwargs):
        book = _Book(os.path.basename(path), path)
        self.append(book)
        return book


class _ExcelHost(ExcelMixin):
    def __init__(self, *books):
        self.app = type("App", (), {})()
        self.app.books = _Books(books)

    def _excel_app(self, launch=False):
        return self.app

    def _excel_state_summary(self, app):
        return "state"


class ExcelTests(_TempCase):

    def test_an_exact_name_wins_and_another_extension_never_matches(self):
        h = _ExcelHost(_Book("Accounts.xlsx"), _Book("Accounts.xlsm"))
        self.assertEqual(h._excel_book("Accounts.xlsm").name, "Accounts.xlsm")
        self.assertEqual(h._excel_book("accounts").name, "Accounts.xlsx")   # stem: first
        only_x = _ExcelHost(_Book("Accounts.xlsx"))
        with self.assertRaises(RuntimeError):
            only_x._excel_book("Accounts.xlsm")

    def test_excel_open_refuses_a_same_named_file_from_another_folder(self):
        users = os.path.join(self.dir, "work", "Budget.xlsx")
        scratch = self.write_bytes("Budget.xlsx", b"")
        h = _ExcelHost(_Book("Budget.xlsx", users))
        out = h.do_excel_open({"path": scratch})
        self.assertIn("DIFFERENT workbook", out)
        same = _ExcelHost(_Book("Budget.xlsx", scratch))
        self.assertIn("Attached to already-open Budget.xlsx", same.do_excel_open({"path": scratch}))

    def test_excel_close_needs_a_name_when_several_are_open(self):
        mine, users = _Book("Scratch.xlsx"), _Book("Personal.xlsx")
        h = _ExcelHost(users, mine)                     # the user's is active
        out = h.do_excel_close({})
        self.assertIn("pass 'workbook'", out)
        self.assertFalse(users.closed)

    def test_a_formula_write_is_not_a_dropped_write(self):
        self.assertFalse(ExcelMixin._excel_write_dropped([['=IF(A1="","",A1)']], [[""]]))
        self.assertTrue(ExcelMixin._excel_write_dropped([["42"]], [[None]]))   # still caught


class CsvSearchTests(_TempCase):

    def test_rows_with_extra_fields_are_searched_not_fatal(self):
        p = self.path("bank.csv")
        with open(p, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows([["Date", "Amount"], ["2026-09-01", "12.50", ""],
                                     ["2026-09-02", "99.00", "ACME refund"]])
        out = stub(DesktopMixin).do_csv_search(p, "refund")
        self.assertIn("Found 1 match", out)


class CompressImageTests(unittest.TestCase):

    def setUp(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        self.Image = Image

    def png(self, img):
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_la_and_16_bit_images_compress(self):
        for mode, value in (("LA", (128, 255)), ("I;16", 40000)):
            with self.subTest(mode=mode):
                data, media = ChatMixin._compress_image(
                    self.png(self.Image.new(mode, (64, 32), value)), 4_000_000)
                self.assertEqual(media, "image/jpeg")
                out = self.Image.open(io.BytesIO(data))
                if mode == "I;16":
                    # scaled to 8 bits (40000/256 = 156), not clipped to white
                    self.assertLess(abs(out.convert("L").getpixel((10, 10)) - 156), 8)

    def test_exif_rotation_is_applied(self):
        img = self.Image.new("RGB", (300, 200), (200, 50, 50))
        exif = img.getexif()
        exif[0x0112] = 6                                 # rotate 90° when shown
        buf = io.BytesIO()
        img.save(buf, format="JPEG", exif=exif)
        data, _media = ChatMixin._compress_image(buf.getvalue(), 4_000_000)
        self.assertEqual(self.Image.open(io.BytesIO(data)).size, (200, 300))


class DesktopTests(unittest.TestCase):

    def test_open_application_args_reach_curated_apps_quoted(self):
        cmd = DesktopMixin._open_app_command("start chrome", 'C:\\a b.txt & calc "x"', True)
        if IS_WINDOWS:
            self.assertEqual(cmd, 'start chrome "C:\\a b.txt & calc x"')
            self.assertEqual(DesktopMixin._open_app_command(r"C:\My Apps\app.exe", "f", False),
                             r'"C:\My Apps\app.exe" "f"')
        else:
            self.assertEqual(DesktopMixin._open_app_command("open -a 'Google Chrome'", "/tmp/a b",
                                                            True),
                             ["open", "-a", "Google Chrome", "/tmp/a b"])

    def test_mouse_scroll_moves_before_it_scrolls(self):
        calls = []
        fake = mock.Mock()
        fake.position.return_value = (1, 1)
        fake.moveTo.side_effect = lambda x, y: calls.append(("moveTo", x, y))
        fake.scroll.side_effect = lambda clicks, **kw: calls.append(("scroll", clicks))
        host = stub(DesktopMixin)
        host._resolve_coord_state = lambda display: (1.0, (0, 0), (100, 100))
        with mock.patch.object(desktop_mixin, "pyautogui", fake, create=True):
            host.do_mouse_scroll(-3, 10, 20)
        self.assertEqual(calls, [("moveTo", 10, 20), ("scroll", -3)])


class DownloadNameTests(_TempCase):

    def test_a_download_never_replaces_a_same_named_file(self):
        first = self.write_bytes("statement.pdf", b"august")
        self.assertEqual(BrowserMixin._browser_free_name(first),
                         self.path("statement (2).pdf"))
        self.write_bytes("statement (2).pdf", b"september")
        self.assertEqual(BrowserMixin._browser_free_name(first),
                         self.path("statement (3).pdf"))
        self.assertEqual(BrowserMixin._browser_free_name(self.path("new.pdf")),
                         self.path("new.pdf"))


class LatexTests(unittest.TestCase):

    def test_sizing_delimiters_and_longer_commands_survive(self):
        f = ChatMixin._latex_to_unicode
        self.assertEqual(f(r"\left( \frac{a}{b} \right)"), "( a/b )")
        self.assertEqual(f(r"\inf S"), "inf S")
        self.assertEqual(f(r"\leftarrow"), "\u2190")
        self.assertEqual(f(r"x \le y, a \in B, \infty"), "x \u2264 y, a \u2208 B, \u221e")


if __name__ == "__main__":
    unittest.main()
