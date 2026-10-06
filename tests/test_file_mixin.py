"""Characterization tests for myagent/file_mixin.py — the native file tools'
safety contracts: exact-unique-match editing that fails loudly, read-before-
edit/overwrite tracking, CRLF and BOM byte-exact round-trips, numbered reads,
and glob/grep pruning — DURING the walk — of .git/.venv-style directories
and of the cloud-synced / network roots a wildcard must never wander into,
plus the walk's STOP and time-ceiling exits (2026-10-04).

FileMixin needs no Tk/App host — a bare subclass instance exercises the do_*
methods directly, with all IO under a TemporaryDirectory."""

import glob
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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


class WalkPruningCase(unittest.TestCase):
    """The 2026-10-04 contract: glob_files / grep_files prune WHILE they walk,
    never enter a remote root from outside, and stop at STOP / the ceiling —
    after a ** glob from ~ crawled the whole OneDrive through the macOS File
    Provider for 50 minutes and looked hung. The walker is hand-rolled, so
    its glob semantics are pinned against glob.glob itself."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.host = _Host()

    def _write(self, rel, data="x"):
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding="utf-8")
        return p

    @staticmethod
    def _paths(result):
        """The absolute paths a glob_files / grep_files result lists."""
        return {ln for ln in result.splitlines()[1:] if ln and not ln.startswith(("⚠", "…"))}

    def _glob(self, pattern, base=None, host=None):
        return (host or self.host).do_glob_files({"pattern": pattern, "path": str(base or self.dir)})

    # ── glob semantics ──────────────────────────────────────────────────

    def test_walker_matches_glob_glob_on_a_plain_tree(self):
        for rel in ("a.py", "b.txt", "src/c.py", "src/d.ts", "src/deep/e.ts",
                    "src/deep/test_f.py", "tests/test_g.py", ".hidden/h.py", ".env",
                    "src/.secret.py", "data/x/y.csv", "data/z/y.csv", "data/z/w.csv"):
            self._write(rel)
        base = str(self.dir)
        for pat in ("*.py", "**/*.py", "src/**/*.ts", "**/test_*.py", "**", "*",
                    "data/*/y.csv", "**/.env", "src/c.py", ".hidden/*.py", "**/.secret.py",
                    "nothing/*.py", "src/**", "**/deep/*.ts", "s*/**/*.py", "data/?/*.csv",
                    "**/*.[ct]s?", "src/./deep/*.py", "a.py"):
            expected = {os.path.abspath(p) for p in glob.glob(os.path.join(base, pat), recursive=True)
                        if os.path.isfile(p)}
            self.assertEqual(self._paths(self._glob(pat)), expected, pat)

    def test_absolute_pattern_overrides_base_and_duplicates_collapse(self):
        self._write("a/a/b.py")
        elsewhere = tempfile.TemporaryDirectory()
        self.addCleanup(elsewhere.cleanup)
        res = self._glob(os.path.join(str(self.dir), "**", "b.py"), base=elsewhere.name)
        self.assertTrue(res.startswith("1 file(s)"), res)
        # a/a/b.py is reachable through two splits of `**/a/**/b.py`; listed once
        res = self._glob("**/a/**/b.py")
        self.assertTrue(res.startswith("1 file(s)"), res)

    def test_newest_first(self):
        old = self._write("old.py")
        new = self._write("new.py")
        os.utime(old, (1_000_000_000, 1_000_000_000))
        os.utime(new, (1_600_000_000, 1_600_000_000))
        lines = self._glob("*.py").splitlines()
        self.assertTrue(lines[1].endswith("new.py") and lines[2].endswith("old.py"), lines)

    # ── pruning while walking ───────────────────────────────────────────

    def _listing(self, run):
        """Every directory os.scandir was asked to list while `run()` ran
        (os.walk lists through scandir too)."""
        listed, real = [], os.scandir

        def recording(path=".", *a, **k):
            listed.append(os.fspath(path))
            return real(path, *a, **k)

        with mock.patch("myagent.file_mixin.os.scandir", recording):
            result = run()
        return result, listed

    def test_skip_dirs_are_never_listed(self):
        self._write("node_modules/pkg/deep/x.py", "beta")
        self._write("build/out.py", "beta")
        self._write("src/ok.py", "beta")
        res, listed = self._listing(lambda: self._glob("**/*.py"))
        self.assertIn("ok.py", res)
        self.assertNotIn("x.py", res)
        self.assertNotIn("out.py", res)
        self.assertFalse([d for d in listed if "node_modules" in d or d.endswith("build")], listed)
        res, listed = self._listing(
            lambda: self.host.do_grep_files({"pattern": "beta", "path": str(self.dir)}))
        self.assertIn("ok.py", res)
        self.assertNotIn("node_modules", res)
        self.assertFalse([d for d in listed if "node_modules" in d or d.endswith("build")], listed)

    def test_naming_a_skip_dir_searches_it(self):
        # The pattern's literal lead-in — or `path` itself — is the walk's
        # root and is never pruned: the rule is about wandering in.
        self._write("node_modules/pkg/x.py")
        self.assertIn("x.py", self._glob("node_modules/pkg/*.py"))
        self.assertIn("x.py", self._glob("*.py", base=self.dir / "node_modules" / "pkg"))

    def test_remote_roots_are_not_entered_from_outside(self):
        cloud = self.dir / "Cloud"
        self._write("Cloud/deep/a.py", "beta")
        self._write("local/b.py", "beta")
        posted = []

        class Host(_Host):
            _file_remote_roots = staticmethod(lambda: {os.path.normcase(str(cloud))})

            def _tool_info(self, message):
                posted.append(message)

        host = Host()
        res = self._glob("**/*.py", host=host)
        self.assertIn("b.py", res)
        self.assertNotIn("a.py", res)
        self.assertIn("⚠ Not entered", res)
        self.assertIn(str(cloud), res)
        self.assertIn("Pass one as 'path' to search it", res)
        self.assertTrue(any("not entering" in m and str(cloud) in m for m in posted), posted)
        # Targeted, it is searched like any folder — as `path` or as a literal lead-in.
        inside = self._glob("**/*.py", base=cloud, host=host)
        self.assertIn("a.py", inside)
        self.assertNotIn("Not entered", inside)
        self.assertIn("a.py", self._glob("Cloud/**/*.py", host=host))
        # grep shares the rule.
        res = host.do_grep_files({"pattern": "beta", "path": str(self.dir)})
        self.assertIn("b.py", res)
        self.assertNotIn("a.py", res)
        self.assertIn("⚠ Not entered", res)
        self.assertIn("a.py", host.do_grep_files({"pattern": "beta", "path": str(cloud)}))

    def test_platform_remote_roots(self):
        roots = FileMixin._file_remote_roots()
        home = os.path.expanduser("~")
        if sys.platform == "darwin":
            # Cloud / network trees, and since 2026-10-06 other apps' sandbox
            # containers — one stat in there raises the per-process "access
            # data from other apps" dialog macOS never remembers.
            for rel in (("Library", "CloudStorage"), ("Library", "Mobile Documents"),
                        ("Library", "Containers"), ("Library", "Group Containers")):
                self.assertIn(os.path.normcase(os.path.join(home, *rel)), roots)
            self.assertIn("/Volumes", roots)
            self.assertEqual(len(roots), 5)
        elif sys.platform == "win32":
            with mock.patch.dict(os.environ, {"OneDrive": r"C:\Users\x\OneDrive"}):
                self.assertIn(os.path.normcase(r"C:\Users\x\OneDrive"), FileMixin._file_remote_roots())
        else:
            self.assertEqual(roots, set())

    def test_wildcards_do_not_follow_symlinked_dirs_but_a_named_link_is_followed(self):
        target = self._write("real/t.py").parent
        link = self.dir / "link"
        try:
            os.symlink(target, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("cannot create a directory symlink here")
        res = self._glob("**/*.py")
        self.assertTrue(res.startswith("1 file(s)"), res)
        self.assertNotIn(os.path.join("link", "t.py"), res)
        self.assertIn(os.path.join("link", "t.py"), self._glob("link/*.py"))

    # ── STOP and the ceiling ────────────────────────────────────────────

    def _stopping_host(self):
        class Host(_Host):
            checks = 0

            @property
            def stop_requested(self):
                self.checks += 1
                return self.checks > 1          # the first directory lists, the next check stops

        return Host()

    def test_stop_ends_the_walk_with_partial_results(self):
        self._write("a.py", "beta")
        self._write("d1/x.py", "beta")
        self._write("d2/y.py", "beta")
        res = self._glob("**/*.py", host=self._stopping_host())
        self.assertTrue(res.startswith("1 file(s)"), res)
        self.assertIn("⚠ Walk ended by STOP — results are PARTIAL.", res)
        res = self._stopping_host().do_grep_files({"pattern": "beta", "path": str(self.dir)})
        self.assertTrue(res.startswith("1 matching file(s)"), res)
        self.assertIn("⚠ Walk ended by STOP", res)

    def test_ceiling_marks_results_partial(self):
        class Host(_Host):
            @staticmethod
            def _file_walk_deadline():
                return time.monotonic() - 1     # already expired

        self._write("a.py", "beta")
        res = self._glob("*.py", host=Host())
        self.assertIn("No files match", res)
        self.assertIn("⚠ Walk stopped after", res)
        self.assertIn("PARTIAL", res)
        res = Host().do_grep_files({"pattern": "beta", "path": str(self.dir)})
        self.assertIn("No matches", res)
        self.assertIn("⚠ Walk stopped after", res)


if __name__ == "__main__":
    unittest.main()
