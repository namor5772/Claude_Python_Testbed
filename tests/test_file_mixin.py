"""Characterization tests for myagent/file_mixin.py — the native file tools'
safety contracts: exact-unique-match editing that fails loudly, read-before-
edit/overwrite tracking, CRLF and BOM byte-exact round-trips, numbered reads,
and glob/grep pruning of .git/.venv-style directories.

FileMixin needs no Tk/App host — a bare subclass instance exercises the do_*
methods directly, with all IO under a TemporaryDirectory."""

import tempfile
import unittest
from pathlib import Path

from myagent.file_mixin import FileMixin, FILE_SKIP_DIRS


class _Host(FileMixin):
    pass


class FileMixinCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.host = _Host()

    def _write(self, name, data, binary=False):
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            p.write_bytes(data)
        else:
            p.write_bytes(data.encode("utf-8"))
        return p

    # ── _file_apply_edit (pure core) ────────────────────────────────────

    def test_edit_unique_replace(self):
        out, n, err = FileMixin._file_apply_edit("a\nbb\nc\n", "bb", "BB")
        self.assertEqual((out, n, err), ("a\nBB\nc\n", 1, None))

    def test_edit_not_found_is_error(self):
        _, n, err = FileMixin._file_apply_edit("abc", "zz", "yy")
        self.assertEqual(n, 0)
        self.assertIn("not found", err)

    def test_edit_ambiguous_is_error_and_names_count(self):
        _, n, err = FileMixin._file_apply_edit("x\nx\nx\n", "x", "y")
        self.assertEqual(n, 0)
        self.assertIn("3 times", err)

    def test_edit_replace_all_counts(self):
        out, n, err = FileMixin._file_apply_edit("x\nx\nx\n", "x", "y", replace_all=True)
        self.assertEqual((out, n, err), ("y\ny\ny\n", 3, None))

    def test_edit_identical_strings_is_error(self):
        _, n, err = FileMixin._file_apply_edit("abc", "b", "b")
        self.assertEqual(n, 0)
        self.assertIn("identical", err)

    def test_edit_empty_old_is_error(self):
        _, n, err = FileMixin._file_apply_edit("abc", "", "b")
        self.assertEqual(n, 0)

    def test_edit_crlf_fallback_preserves_line_endings(self):
        # LF-normalized old/new against a CRLF file: the fallback expands both
        # to \r\n so the file keeps its real line endings after the edit.
        content = "one\r\ntwo\r\nthree\r\n"
        out, n, err = FileMixin._file_apply_edit(content, "one\ntwo", "one\nTWO")
        self.assertIsNone(err)
        self.assertEqual(out, "one\r\nTWO\r\nthree\r\n")

    def test_edit_crlf_fallback_not_applied_when_old_has_cr(self):
        # old_string already carries \r: the model saw the real endings, so a
        # failed match is a genuine mismatch, not normalization.
        _, n, err = FileMixin._file_apply_edit("one\r\ntwo\r\n", "one\r\nX", "y")
        self.assertEqual(n, 0)
        self.assertIn("not found", err)

    # ── _file_numbered (pure core) ──────────────────────────────────────

    def test_numbered_format_and_window(self):
        body, total, first, last = FileMixin._file_numbered("a\nb\nc\nd", offset=2, limit=2)
        self.assertEqual(total, 4)
        self.assertEqual((first, last), (2, 3))
        self.assertEqual(body, "     2\tb\n     3\tc")

    def test_numbered_offset_past_end(self):
        body, total, first, last = FileMixin._file_numbered("a\nb", offset=10, limit=5)
        self.assertEqual(body, "")
        self.assertEqual((total, first, last), (2, 0, 0))

    def test_numbered_truncates_long_lines(self):
        body, _, _, _ = FileMixin._file_numbered("x" * 600)
        self.assertIn("[line truncated]", body)
        self.assertNotIn("x" * 501, body)

    # ── read → edit → write contracts (IO) ──────────────────────────────

    def test_edit_refused_without_prior_read(self):
        p = self._write("f.py", "a = 1\n")
        res = self.host.do_edit_file({"path": str(p), "old_string": "a = 1", "new_string": "a = 2"})
        self.assertIn("read_file", res)
        self.assertEqual(p.read_bytes(), b"a = 1\n")  # untouched

    def test_read_then_edit_succeeds(self):
        p = self._write("f.py", "a = 1\n")
        read = self.host.do_read_file({"path": str(p)})
        self.assertIn("1\ta = 1", read)
        res = self.host.do_edit_file({"path": str(p), "old_string": "a = 1", "new_string": "a = 2"})
        self.assertIn("Replaced 1 occurrence", res)
        self.assertEqual(p.read_bytes(), b"a = 2\n")

    def test_edit_round_trips_crlf_and_bom_byte_exactly(self):
        raw = ("\ufeff" + "line1\r\nline2\r\n").encode("utf-8")
        p = self._write("bom.ps1", raw, binary=True)
        self.host.do_read_file({"path": str(p)})
        res = self.host.do_edit_file({"path": str(p), "old_string": "line2", "new_string": "LINE2"})
        self.assertIn("Replaced", res)
        self.assertEqual(p.read_bytes(), ("\ufeff" + "line1\r\nLINE2\r\n").encode("utf-8"))

    def test_write_new_file_creates_parents(self):
        p = self.dir / "sub" / "new.txt"
        res = self.host.do_write_file({"path": str(p), "content": "hi\n"})
        self.assertIn("Created", res)
        self.assertEqual(p.read_bytes(), b"hi\n")

    def test_write_refuses_unread_overwrite_then_allows_after_read(self):
        p = self._write("keep.txt", "original")
        res = self.host.do_write_file({"path": str(p), "content": "clobber"})
        self.assertIn("refused", res)
        self.assertEqual(p.read_bytes(), b"original")
        self.host.do_read_file({"path": str(p)})
        res = self.host.do_write_file({"path": str(p), "content": "clobber"})
        self.assertIn("Overwrote", res)
        self.assertEqual(p.read_bytes(), b"clobber")

    def test_read_missing_and_binary(self):
        self.assertIn("not found", self.host.do_read_file({"path": str(self.dir / "no.txt")}))
        p = self._write("bin.dat", b"ab\x00cd", binary=True)
        self.assertIn("binary", self.host.do_read_file({"path": str(p)}))

    def test_edit_rejects_non_utf8(self):
        p = self._write("latin.txt", b"caf\xe9\n", binary=True)
        self.host.do_read_file({"path": str(p)})  # replace-mode read is allowed
        res = self.host.do_edit_file({"path": str(p), "old_string": "caf", "new_string": "x"})
        self.assertIn("not valid UTF-8", res)

    # ── glob / grep ─────────────────────────────────────────────────────

    def test_glob_skips_pruned_dirs_and_sorts(self):
        self._write("a.py", "1")
        self._write(".venv/lib/deep.py", "1")
        self._write("node_modules/pkg/x.py", "1")
        self._write("src/b.py", "1")
        res = self.host.do_glob_files({"pattern": "**/*.py", "path": str(self.dir)})
        self.assertIn("a.py", res)
        self.assertIn("b.py", res)
        self.assertNotIn("deep.py", res)
        self.assertNotIn("node_modules", res)
        self.assertTrue(res.startswith("2 file(s)"))

    def test_glob_no_match_and_bad_base(self):
        self.assertIn("No files match", self.host.do_glob_files({"pattern": "*.zzz", "path": str(self.dir)}))
        self.assertIn("not a directory", self.host.do_glob_files({"pattern": "*", "path": str(self.dir / "nope")}))

    def test_grep_modes_and_pruning(self):
        self._write("one.py", "alpha = 1\nbeta = 2\n")
        self._write("two.py", "beta = 3\nbeta = 4\n")
        self._write(".git/three.py", "beta = 5\n")
        base = {"pattern": r"beta = \d", "path": str(self.dir)}
        files = self.host.do_grep_files(base)
        self.assertIn("one.py", files)
        self.assertIn("two.py", files)
        self.assertNotIn(".git", files)
        content = self.host.do_grep_files({**base, "output_mode": "content"})
        self.assertIn("two.py:1: beta = 3", content)
        count = self.host.do_grep_files({**base, "output_mode": "count", "glob": "two.py"})
        self.assertIn("two.py: 2", count)
        self.assertNotIn("one.py", count)

    def test_grep_single_file_ignore_case_and_bad_regex(self):
        p = self._write("f.txt", "Hello\n")
        hit = self.host.do_grep_files({"pattern": "hello", "path": str(p), "ignore_case": True})
        self.assertIn("f.txt", hit)
        miss = self.host.do_grep_files({"pattern": "hello", "path": str(p)})
        self.assertIn("No matches", miss)
        bad = self.host.do_grep_files({"pattern": "(", "path": str(p)})
        self.assertIn("invalid regex", bad)

    def test_grep_skips_binary(self):
        self._write("b.bin", b"be\x00ta", binary=True)
        self._write("t.txt", "beta\n")
        res = self.host.do_grep_files({"pattern": "beta", "path": str(self.dir)})
        self.assertIn("t.txt", res)
        self.assertNotIn("b.bin", res)

    def test_skip_dirs_include_the_heavy_hitters(self):
        for d in (".git", ".venv", "node_modules", "__pycache__", ".claude"):
            self.assertIn(d, FILE_SKIP_DIRS)


if __name__ == "__main__":
    unittest.main()
