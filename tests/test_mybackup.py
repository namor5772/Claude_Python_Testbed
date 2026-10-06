"""Characterization tests for MyBackup (2026-10-03).

MyBackup mirrors a FROM / TO table of directories; because a mirror DELETES
whatever TO holds that FROM does not, the engine is kept pure (no Tk) and
pinned here on real temp trees: the setup file round trip, every row-level
safety rule and the cross-row TO clash, the plan (size + mtime comparison,
type swaps, deepest-first directory deletes, deletions suppressed after an
incomplete FROM scan), the execution (read-only files on Windows, links left
alone, cancellation), run_rows's one confirmation and per-row independence,
the headless entry point's exit codes, and the shared line formatting.

MyBackup is importable in-process: the module builds no Tk root (App is only
constructed in main()), like SelfBot.
"""

import contextlib
import csv
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import MyBackup as mb


def make_tree(root, spec):
    """Build files and directories under root from {rel: content}; a rel
    ending in '/' is an (empty) directory. Rel paths use '/'."""
    for rel, content in spec.items():
        path = os.path.join(root, *rel.strip("/").split("/"))
        if rel.endswith("/"):
            os.makedirs(path, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)


def read_tree(root):
    """{rel: content} for every file and {rel: None} for every directory under
    root — rel with '/' — so two trees compare with one assertEqual."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames:
            out[os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, "/")] = None
        for name in filenames:
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as f:
                out[os.path.relpath(path, root).replace(os.sep, "/")] = f.read()
    return out


def rel(*parts):
    return os.path.join(*parts)


def set_mtime(path, when):
    os.utime(path, (when, when))


def can_symlink(base):
    """Whether this account may create symlinks (Windows needs Developer Mode
    or a privilege); tests that need one skip otherwise."""
    target = os.path.join(base, "_probe_target")
    link = os.path.join(base, "_probe_link")
    os.makedirs(target, exist_ok=True)
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        return False
    # Removed the way the engine removes a link (_remove_file): a plain
    # remove — a link is a file on POSIX, where rmdir on one is ENOTDIR and
    # errored this probe on macOS (2026-10-06) — with Windows' rmdir for a
    # link to a directory as the fallback.
    try:
        os.remove(link)
    except PermissionError:
        os.rmdir(link)
    return True


class TempTreeCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = self._tmp.name
        self.src = os.path.join(self.base, "src")
        self.dst = os.path.join(self.base, "dst")  # NOT created: the mirror does that
        os.makedirs(self.src)

    def tearDown(self):
        for dirpath, _dirnames, filenames in os.walk(self.base):
            for name in filenames:
                try:
                    os.chmod(os.path.join(dirpath, name), stat.S_IWRITE)
                except OSError:
                    pass
        self._tmp.cleanup()

    def mirror(self, **kwargs):
        plan = mb.plan_mirror(self.src, self.dst, row=1)
        return plan, mb.execute_plan(plan, **kwargs)


# ── Setup file ────────────────────────────────────────────────────────

class SetupFileTests(TempTreeCase):
    def test_round_trip_keeps_rows_and_quotes_a_comma(self):
        rows = [["C:\\Users\\me\\OneDrive\\MyAppShare\\", "D:\\MyAppShare\\"],
                ["/Users/me/a,b", "/Volumes/Backup/a,b"]]
        path = os.path.join(self.base, "setup.csv")
        mb.save_setup(path, rows)
        with open(path, encoding="utf-8") as f:
            first = f.readline().strip()
        self.assertEqual(first, "FROM,TO")
        self.assertEqual(mb.load_setup(path), rows)
        self.assertFalse([n for n in os.listdir(self.base) if n.endswith(".tmp")], "temp file left behind")

    def test_header_is_optional_and_rows_are_padded_to_two_cells(self):
        path = os.path.join(self.base, "setup.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as f:  # a BOM too
            csv.writer(f).writerows([["only-from"], ["a", "b", "extra"], []])
        self.assertEqual(mb.load_setup(path), [["only-from", ""], ["a", "b"], ["", ""]])

    def test_header_match_is_case_and_space_insensitive(self):
        path = os.path.join(self.base, "setup.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows([[" from ", "To"], ["a", "b"]])
        self.assertEqual(mb.load_setup(path), [["a", "b"]])

    def test_resolve_setup_path_order(self):
        state = os.path.join(self.base, "state.json")
        default = os.path.join(self.base, "backup_setup.csv")
        named = os.path.join(self.base, "named.csv")
        with mock.patch.object(mb, "STATE_FILE", state), mock.patch.object(mb, "DEFAULT_SETUP_FILE", default):
            self.assertIsNone(mb.resolve_setup_path(os.path.join(self.base, "missing.csv")))
            self.assertIsNone(mb.resolve_setup_path(), "nothing exists yet")
            mb.save_setup(default, [["a", "b"]])
            self.assertEqual(mb.resolve_setup_path(), default)
            mb.save_setup(named, [["a", "b"]])
            with open(state, "w", encoding="utf-8") as f:
                json.dump({"filepath": named}, f)
            self.assertEqual(mb.resolve_setup_path(), named, "the file last open in the GUI wins")
            os.remove(named)
            self.assertEqual(mb.resolve_setup_path(), default, "a vanished last file falls back to the default")
            self.assertEqual(mb.resolve_setup_path(default), default, "explicit path wins over the state")


# ── Row validation ────────────────────────────────────────────────────

class ValidateRowTests(TempTreeCase):
    def test_normalize_dir(self):
        self.assertEqual(mb.normalize_dir("   "), "")
        quoted = f'"{self.src}{os.sep}"'
        self.assertEqual(mb.normalize_dir(quoted), self.src, "quotes and the trailing separator go")
        self.assertEqual(mb.normalize_dir("~"), os.path.normpath(os.path.expanduser("~")))

    def test_is_within_is_component_wise_and_case_folded(self):
        a = os.path.join(self.base, "a")
        self.assertTrue(mb.is_within(os.path.join(a, "deep", "er"), a))
        self.assertTrue(mb.is_within(a, a))
        self.assertFalse(mb.is_within(os.path.join(self.base, "ab"), a), "C:\\ab is not inside C:\\a")
        self.assertFalse(mb.is_within(a, os.path.join(a, "deep")))
        if sys.platform == "win32":
            self.assertTrue(mb.is_within(os.path.join(a, "X").upper(), a.lower()))

    def test_is_filesystem_root(self):
        self.assertTrue(mb.is_filesystem_root(os.path.abspath(os.sep)))
        self.assertFalse(mb.is_filesystem_root(self.base))
        if sys.platform == "win32":
            self.assertTrue(mb.is_filesystem_root("C:\\"))
            self.assertTrue(mb.is_filesystem_root("\\\\server\\share"))

    def test_empty_cells(self):
        self.assertEqual(mb.validate_row("", self.dst), "FROM is empty")
        self.assertEqual(mb.validate_row(self.src, ""), "TO is empty")

    def test_from_must_be_an_existing_directory(self):
        missing = os.path.join(self.base, "nope")
        self.assertIn("not an existing directory", mb.validate_row(missing, self.dst))
        a_file = os.path.join(self.base, "file.txt")
        make_tree(self.base, {"file.txt": "x"})
        self.assertIn("not an existing directory", mb.validate_row(a_file, self.dst))

    def test_to_equal_to_or_nested_with_from(self):
        self.assertIn("same directory", mb.validate_row(self.src, self.src))
        if sys.platform == "win32":
            self.assertIn("same directory", mb.validate_row(self.src, self.src.upper()))
        inside = os.path.join(self.src, "backup")
        self.assertIn("TO is inside FROM", mb.validate_row(self.src, inside))
        os.makedirs(inside)
        self.assertIn("FROM is inside TO", mb.validate_row(inside, self.base))

    def test_to_root_and_home_are_refused_with_their_own_reason(self):
        root = os.path.abspath(os.sep)
        self.assertIn("filesystem root", mb.validate_row(self.src, root), "root beats 'FROM is inside TO'")
        home = os.path.join(self.base, "home")
        os.makedirs(home)
        env = {"HOME": home, "USERPROFILE": home}
        with mock.patch.dict(os.environ, env):
            src_in_home = os.path.join(home, "docs")
            os.makedirs(src_in_home)
            self.assertIn("home directory", mb.validate_row(src_in_home, home))

    def test_to_parent_must_exist_and_to_must_not_be_a_file(self):
        orphan = os.path.join(self.base, "unmounted", "Backup")
        reason = mb.validate_row(self.src, orphan)
        self.assertIn("parent folder does not exist", reason)
        self.assertIn(os.path.join(self.base, "unmounted"), reason)
        make_tree(self.base, {"to.txt": "x"})
        self.assertIn("not a directory", mb.validate_row(self.src, os.path.join(self.base, "to.txt")))

    def test_valid_rows(self):
        self.assertIsNone(mb.validate_row(self.src, self.dst), "TO need not exist yet — its parent does")
        os.makedirs(self.dst)
        self.assertIsNone(mb.validate_row(self.src, self.dst))


class ValidateRowsTests(TempTreeCase):
    def test_blank_rows_skipped_and_table_positions_kept(self):
        rows = [[self.src, self.dst], ["", ""], ["  ", ""], [self.src, os.path.join(self.base, "dst2")]]
        active, errors = mb.validate_rows(rows)
        self.assertEqual(errors, [])
        self.assertEqual([a[0] for a in active], [1, 4], "row numbers are table positions, blanks included")
        self.assertEqual(active[0][1:], (self.src, self.dst))

    def test_short_rows_tolerated(self):
        active, errors = mb.validate_rows([[self.src], []])
        self.assertEqual(active, [])
        self.assertEqual(errors, [(1, "TO is empty")])

    def test_same_or_nested_to_refuses_both_rows_and_spares_the_rest(self):
        src2 = os.path.join(self.base, "src2")
        src3 = os.path.join(self.base, "src3")
        os.makedirs(src2)
        os.makedirs(src3)
        os.makedirs(self.dst)  # so row 2's TO passes the parent-exists rule and reaches the clash check
        rows = [[self.src, self.dst],
                [src2, os.path.join(self.dst, "sub") + os.sep],  # nested in row 1's TO
                [src3, os.path.join(self.base, "dst3")]]
        active, errors = mb.validate_rows(rows)
        self.assertEqual([a[0] for a in active], [3])
        self.assertEqual([e[0] for e in errors], [1, 2])
        self.assertIn("row 2's TO", errors[0][1])
        self.assertIn("row 1's TO", errors[1][1])

        rows = [[self.src, self.dst], [src2, self.dst]]
        active, errors = mb.validate_rows(rows)
        self.assertEqual(active, [])
        self.assertEqual(len(errors), 2)

    def test_own_errors_come_with_row_numbers(self):
        rows = [[self.src, ""], ["", ""], [os.path.join(self.base, "nope"), self.dst]]
        active, errors = mb.validate_rows(rows)
        self.assertEqual(active, [])
        self.assertEqual([e[0] for e in errors], [1, 3])


# ── Plan ──────────────────────────────────────────────────────────────

class PlanTests(TempTreeCase):
    def test_fresh_destination(self):
        make_tree(self.src, {"a.txt": "aaa", "sub/b.txt": "bb", "sub/deep/c.txt": "c", "empty/": None})
        plan = mb.plan_mirror(self.src, self.dst)
        self.assertEqual(plan.copies, [(rel("a.txt"), 3), (rel("sub", "b.txt"), 2), (rel("sub", "deep", "c.txt"), 1)])
        self.assertEqual(plan.copy_bytes, 6)
        self.assertEqual(plan.mkdirs, [rel("empty"), rel("sub"), rel("sub", "deep")], "shallowest first")
        self.assertEqual((plan.src_files, plan.src_dirs, plan.unchanged), (3, 3, 0))
        self.assertEqual((plan.delete_files, plan.delete_dirs, plan.errors), ([], [], []))

    def test_unchanged_changed_and_extra(self):
        make_tree(self.src, {"same.txt": "same", "newer.txt": "v2", "resized.txt": "longer"})
        make_tree(self.dst, {"same.txt": "same", "newer.txt": "v1", "resized.txt": "short",
                             "extra.txt": "x", "gone/inner/deep.txt": "d", "gone/empty/": None})
        now = time.time() - 100
        for name in ("same.txt", "newer.txt", "resized.txt"):
            set_mtime(os.path.join(self.src, name), now)
        set_mtime(os.path.join(self.dst, "same.txt"), now + 1.5)  # within the FAT tolerance
        set_mtime(os.path.join(self.dst, "newer.txt"), now - 10)  # same size, older
        set_mtime(os.path.join(self.dst, "resized.txt"), now)  # same time, other size
        plan = mb.plan_mirror(self.src, self.dst)
        self.assertEqual(plan.unchanged, 1)
        self.assertEqual([c[0] for c in plan.copies], [rel("newer.txt"), rel("resized.txt")])
        self.assertEqual(plan.delete_files, [rel("extra.txt"), rel("gone", "inner", "deep.txt")])
        self.assertEqual(plan.delete_dirs, [rel("gone", "empty"), rel("gone", "inner"), rel("gone")], "deepest first")
        self.assertEqual(plan.deletions, 5)

    def test_mtime_beyond_tolerance_copies(self):
        make_tree(self.src, {"f.txt": "x"})
        make_tree(self.dst, {"f.txt": "x"})
        now = time.time() - 100
        set_mtime(os.path.join(self.src, "f.txt"), now)
        set_mtime(os.path.join(self.dst, "f.txt"), now - mb.MTIME_TOLERANCE - 1)
        self.assertEqual([c[0] for c in mb.plan_mirror(self.src, self.dst).copies], [rel("f.txt")])

    def test_type_swaps(self):
        make_tree(self.src, {"was_dir": "now a file", "was_file/": None})
        make_tree(self.dst, {"was_dir/inner.txt": "i", "was_file": "f"})
        plan = mb.plan_mirror(self.src, self.dst)
        self.assertEqual(plan.delete_files, [rel("was_dir", "inner.txt"), rel("was_file")])
        self.assertEqual(plan.delete_dirs, [rel("was_dir")])
        self.assertEqual(plan.mkdirs, [rel("was_file")])
        self.assertEqual([c[0] for c in plan.copies], [rel("was_dir")])

    def test_incomplete_from_scan_suppresses_deletions(self):
        make_tree(self.src, {"ok/a.txt": "a", "locked/secret.txt": "s"})
        make_tree(self.dst, {"ok/a.txt": "a", "locked/secret.txt": "s", "stale.txt": "old", "olddir/": None})
        shutil.copystat(os.path.join(self.src, "ok", "a.txt"), os.path.join(self.dst, "ok", "a.txt"))
        blocked = os.path.normcase(os.path.join(self.src, "locked"))
        real_scandir = os.scandir

        def fake_scandir(path="."):
            if os.path.normcase(str(path)) == blocked:
                raise PermissionError(13, "Access is denied")
            return real_scandir(path)

        with mock.patch("os.scandir", new=fake_scandir):
            plan = mb.plan_mirror(self.src, self.dst)
        self.assertEqual(len(plan.errors), 1)
        self.assertIn("cannot list", plan.errors[0])
        self.assertEqual((plan.delete_files, plan.delete_dirs), ([], []))
        # stale.txt, olddir AND the unlisted locked/secret.txt are all kept (the
        # locked folder itself was listed from the root; only its contents failed)
        self.assertEqual(plan.suppressed_deletes, 3)
        self.assertEqual([c[0] for c in plan.copies], [], "the listed, unchanged file is still compared")


# ── Execute ───────────────────────────────────────────────────────────

class ExecuteTests(TempTreeCase):
    SPEC = {"a.txt": "alpha", "sub/b.txt": "beta", "sub/deep/c.txt": "gamma", "empty/": None,
            "unicode-ü/ñ.txt": "ok"}

    def test_mirror_makes_the_trees_identical_and_a_rerun_copies_nothing(self):
        make_tree(self.src, self.SPEC)
        plan, result = self.mirror()
        self.assertEqual(read_tree(self.dst), read_tree(self.src))
        self.assertEqual((result.copied, result.made_dirs, result.deleted_files, result.deleted_dirs),
                         (4, 4, 0, 0))
        self.assertEqual(result.copied_bytes, plan.copy_bytes)
        self.assertEqual(result.errors, [])
        src_stat = os.stat(os.path.join(self.src, "a.txt"))
        dst_stat = os.stat(os.path.join(self.dst, "a.txt"))
        self.assertAlmostEqual(src_stat.st_mtime, dst_stat.st_mtime, delta=mb.MTIME_TOLERANCE,
                               msg="copy2 keeps the modification time, the basis of the next comparison")
        plan2, result2 = self.mirror()
        self.assertEqual((plan2.copies, plan2.unchanged, plan2.deletions), ([], 4, 0))
        self.assertEqual((result2.copied, result2.deleted_files), (0, 0))

    def test_extras_and_type_swaps_are_removed(self):
        make_tree(self.src, {"keep.txt": "k", "was_dir": "file now", "was_file/": None})
        make_tree(self.dst, {"keep.txt": "k", "stale.txt": "s", "old/x/y.txt": "y", "old/empty/": None,
                             "was_dir/inner.txt": "i", "was_file": "f"})
        shutil.copystat(os.path.join(self.src, "keep.txt"), os.path.join(self.dst, "keep.txt"))
        plan, result = self.mirror()
        self.assertEqual(read_tree(self.dst), read_tree(self.src))
        self.assertEqual((result.deleted_files, result.deleted_dirs, result.copied, result.made_dirs), (4, 4, 1, 1))
        self.assertEqual(result.errors, [])

    def test_read_only_files_are_deleted_and_overwritten(self):
        make_tree(self.src, {"ro.txt": "new content"})
        make_tree(self.dst, {"ro.txt": "old", "stale_ro.txt": "x"})
        for name in ("ro.txt", "stale_ro.txt"):
            os.chmod(os.path.join(self.dst, name), stat.S_IREAD)
        set_mtime(os.path.join(self.dst, "ro.txt"), time.time() - 500)
        plan, result = self.mirror()
        self.assertEqual(result.errors, [])
        self.assertEqual(read_tree(self.dst), {"ro.txt": "new content"})

    def test_cancel_before_copying_leaves_the_destination_alone(self):
        make_tree(self.src, {"a.txt": "a", "b.txt": "b"})
        cancel = threading.Event()
        cancel.set()
        plan, result = self.mirror(cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.copied, 0)
        self.assertEqual(read_tree(self.dst), {}, "only the TO directory itself was created")

    def test_cancel_mid_copy_stops_after_the_current_file(self):
        make_tree(self.src, {"a.txt": "a", "b.txt": "b", "c.txt": "c"})
        cancel = threading.Event()
        seen = []

        def report(kind, **data):
            if kind == "file":
                seen.append(data["index"])
                if data["index"] == 1:
                    cancel.set()

        plan, result = self.mirror(report=report, cancel=cancel)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.copied, 2, "the file in flight finishes; the next never starts")
        self.assertEqual(seen, [0, 1])

    def test_progress_events_cover_every_file_and_end_on_the_total(self):
        make_tree(self.src, {"a.txt": "aa", "b.txt": "bbb"})
        events = []
        plan, result = self.mirror(report=lambda kind, **data: events.append((kind, data)))
        files = [d for k, d in events if k == "file"]
        self.assertEqual([(f["index"], f["done_bytes"]) for f in files], [(0, 0), (1, 2), (2, 5)])
        self.assertTrue(all(f["total_bytes"] == 5 and f["total"] == 2 for f in files))
        self.assertEqual([k for k, _ in events if k == "status"], [], "no deletes, no status line")

    def test_links_are_skipped_not_followed(self):
        if not can_symlink(self.base):
            self.skipTest("this account cannot create symlinks")
        make_tree(self.src, {"real/file.txt": "r"})
        os.symlink(os.path.join(self.src, "real"), os.path.join(self.src, "link"), target_is_directory=True)
        plan, result = self.mirror()
        self.assertEqual(read_tree(self.dst), {"real": None, "real/file.txt": "r"})
        self.assertEqual(len(plan.warnings), 1)
        self.assertIn("skipped link", plan.warnings[0])
        # an extra link in TO is removed as a link — its target is untouched
        target = os.path.join(self.base, "elsewhere")
        make_tree(target, {"precious.txt": "keep me"})
        os.symlink(target, os.path.join(self.dst, "stray"), target_is_directory=True)
        os.makedirs(os.path.join(self.dst, "stray_dir_extra"))
        plan, result = self.mirror()
        self.assertEqual(result.errors, [])
        self.assertFalse(os.path.lexists(os.path.join(self.dst, "stray")))
        self.assertFalse(os.path.exists(os.path.join(self.dst, "stray_dir_extra")))
        self.assertEqual(read_tree(target), {"precious.txt": "keep me"})

    def test_errors_are_recorded_per_item_and_the_run_goes_on(self):
        make_tree(self.src, {"a.txt": "a", "b.txt": "b"})
        real_copy2 = shutil.copy2

        def failing_copy2(src, dst, **kw):
            if src.endswith("a.txt"):
                raise PermissionError(32, "The process cannot access the file because it is being used")
            return real_copy2(src, dst, **kw)

        with mock.patch("shutil.copy2", new=failing_copy2):
            plan, result = self.mirror()
        self.assertEqual(result.copied, 1)
        self.assertEqual(len(result.errors), 1)
        self.assertIn("cannot copy", result.errors[0])
        self.assertEqual(read_tree(self.dst), {"b.txt": "b"})


# ── run_rows ──────────────────────────────────────────────────────────

class RunRowsTests(TempTreeCase):
    def record(self):
        events = []
        return events, (lambda kind, **data: events.append((kind, data)))

    def kinds(self, events):
        return [k for k, _ in events if k not in ("file", "status")]

    def test_a_bad_row_is_reported_and_the_good_rows_still_run(self):
        make_tree(self.src, {"a.txt": "a"})
        orphan = os.path.join(self.base, "unplugged", "Backup")
        events, report = self.record()
        summary = mb.run_rows([[self.src, self.dst], [self.src, orphan]], report)
        self.assertEqual(self.kinds(events), ["row_error", "row_start", "row_plan", "row_done", "summary"])
        self.assertEqual(events[0][1]["row"], 2)
        self.assertEqual((summary.rows, summary.rows_ok, summary.rows_failed), (2, 1, 1))
        self.assertFalse(summary.ok)
        self.assertEqual(read_tree(self.dst), {"a.txt": "a"})

    def test_blank_rows_do_not_count(self):
        make_tree(self.src, {"a.txt": "a"})
        events, report = self.record()
        summary = mb.run_rows([["", ""], [self.src, self.dst], ["", ""]], report)
        self.assertEqual((summary.rows, summary.rows_ok), (1, 1))
        self.assertTrue(summary.ok)
        self.assertEqual(events[0][1]["row"], 2, "numbered by table position")

    def test_confirm_is_asked_only_when_something_would_be_deleted(self):
        make_tree(self.src, {"a.txt": "a"})
        asked = []

        def confirm(plans):
            asked.append([p.row for p in plans])
            return True

        events, report = self.record()
        mb.run_rows([[self.src, self.dst]], report, confirm=confirm)
        self.assertEqual(asked, [], "a first copy into an empty TO deletes nothing")
        make_tree(self.dst, {"stale.txt": "s"})
        mb.run_rows([[self.src, self.dst]], report, confirm=confirm)
        self.assertEqual(asked, [[1]])
        self.assertEqual(read_tree(self.dst), {"a.txt": "a"})

    def test_a_declined_confirmation_changes_nothing(self):
        make_tree(self.src, {"a.txt": "a", "new.txt": "n"})
        make_tree(self.dst, {"a.txt": "old", "stale.txt": "s"})
        before = read_tree(self.dst)
        events, report = self.record()
        summary = mb.run_rows([[self.src, self.dst]], report, confirm=lambda plans: False)
        self.assertTrue(summary.cancelled)
        self.assertFalse(summary.ok)
        self.assertEqual(read_tree(self.dst), before)
        cancelled = [d for k, d in events if k == "cancelled"]
        self.assertEqual(cancelled, [{"before_any": True}])
        self.assertEqual(self.kinds(events)[-1], "cancelled", "no summary line after a declined run")

    def test_dry_run_plans_everything_and_changes_nothing(self):
        make_tree(self.src, {"a.txt": "a"})
        make_tree(self.dst, {"stale.txt": "s"})
        events, report = self.record()
        summary = mb.run_rows([[self.src, self.dst]], report, confirm=lambda plans: self.fail("asked in a dry run"),
                              dry_run=True)
        self.assertTrue(summary.dry_run)
        self.assertEqual(self.kinds(events), ["row_start", "row_plan", "summary"])
        self.assertEqual(read_tree(self.dst), {"stale.txt": "s"})
        self.assertEqual(len(events[-1][1]["plans"]), 1)

    def test_two_rows_are_mirrored_independently(self):
        src2 = os.path.join(self.base, "src2")
        dst2 = os.path.join(self.base, "dst2")
        make_tree(self.src, {"a.txt": "a"})
        make_tree(src2, {"b/c.txt": "c"})
        events, report = self.record()
        summary = mb.run_rows([[self.src, self.dst], [src2, dst2]], report)
        self.assertTrue(summary.ok)
        self.assertEqual((summary.rows_ok, summary.copied), (2, 2))
        self.assertEqual(read_tree(self.dst), {"a.txt": "a"})
        self.assertEqual(read_tree(dst2), {"b": None, "b/c.txt": "c"})

    def test_cancel_set_before_the_run_does_nothing(self):
        make_tree(self.src, {"a.txt": "a"})
        cancel = threading.Event()
        cancel.set()
        events, report = self.record()
        summary = mb.run_rows([[self.src, self.dst]], report, cancel=cancel)
        self.assertTrue(summary.cancelled)
        self.assertFalse(os.path.exists(self.dst))


# ── Headless entry point ──────────────────────────────────────────────

class HeadlessTests(TempTreeCase):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.base, "logs", "mybackup.log")
        self.state = os.path.join(self.base, "state.json")
        self.default = os.path.join(self.base, "backup_setup.csv")
        patches = [mock.patch.object(mb, "LOG_FILE", self.log),
                   mock.patch.object(mb, "STATE_FILE", self.state),
                   mock.patch.object(mb, "DEFAULT_SETUP_FILE", self.default)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mb.main(list(args))
        return code, out.getvalue()

    def test_headless_mirrors_the_setup_and_logs(self):
        make_tree(self.src, {"a.txt": "a", "sub/b.txt": "b"})
        setup = os.path.join(self.base, "setup.csv")
        mb.save_setup(setup, [[self.src, self.dst]])
        code, out = self.run_main("--headless", "--setup", setup)
        self.assertEqual(code, 0, out)
        self.assertEqual(read_tree(self.dst), read_tree(self.src))
        self.assertIn("=== MyBackup", out)
        self.assertIn("Done: 1 of 1 row OK", out)
        with open(self.log, encoding="utf-8") as f:
            logged = f.read()
        self.assertIn("Done: 1 of 1 row OK", logged)
        self.assertRegex(logged.splitlines()[0], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}  === MyBackup")

    def test_headless_uses_the_default_setup_when_no_setup_is_given(self):
        make_tree(self.src, {"a.txt": "a"})
        mb.save_setup(self.default, [[self.src, self.dst]])
        code, out = self.run_main("--headless")
        self.assertEqual(code, 0, out)
        self.assertEqual(read_tree(self.dst), {"a.txt": "a"})

    def test_a_refused_row_is_exit_code_1(self):
        make_tree(self.src, {"a.txt": "a"})
        setup = os.path.join(self.base, "setup.csv")
        mb.save_setup(setup, [[self.src, self.dst], [self.src, os.path.join(self.base, "x", "y")]])
        code, out = self.run_main("--headless", "--setup", setup)
        self.assertEqual(code, 1)
        self.assertIn("Row 2: ERROR", out)
        self.assertEqual(read_tree(self.dst), {"a.txt": "a"}, "the good row still ran")

    def test_no_setup_is_exit_code_2(self):
        code, out = self.run_main("--headless")
        self.assertEqual(code, 2)
        self.assertIn("no setup file", out)
        code, out = self.run_main("--headless", "--setup", os.path.join(self.base, "missing.csv"))
        self.assertEqual(code, 2)
        empty = os.path.join(self.base, "empty.csv")
        mb.save_setup(empty, [["", ""]])
        code, out = self.run_main("--headless", "--setup", empty)
        self.assertEqual(code, 2)
        self.assertIn("no rows", out)

    def test_dry_run_implies_headless_and_changes_nothing(self):
        make_tree(self.src, {"a.txt": "a"})
        make_tree(self.dst, {"stale.txt": "s"})
        setup = os.path.join(self.base, "setup.csv")
        mb.save_setup(setup, [[self.src, self.dst]])
        code, out = self.run_main("--dry-run", "--setup", setup)
        self.assertEqual(code, 0, out)
        self.assertIn("DRY RUN", out)
        self.assertIn("Dry run: 1 row planned", out)
        self.assertEqual(read_tree(self.dst), {"stale.txt": "s"})

    def test_log_rotates_in_one_slot(self):
        os.makedirs(os.path.dirname(self.log))
        with open(self.log, "w", encoding="utf-8") as f:
            f.write("x" * (mb.LOG_MAX_BYTES + 1))
        mb.LogFile().write([("fresh line", "plain")])
        with open(self.log, encoding="utf-8") as f:
            self.assertIn("fresh line", f.read())
        self.assertEqual(os.path.getsize(self.log + ".1"), mb.LOG_MAX_BYTES + 1)


# ── Formatting ────────────────────────────────────────────────────────

class FormatTests(unittest.TestCase):
    def test_fmt_bytes_and_elapsed(self):
        self.assertEqual(mb.fmt_bytes(0), "0 B")
        self.assertEqual(mb.fmt_bytes(1023), "1,023 B")
        self.assertEqual(mb.fmt_bytes(1536), "1.5 KB")
        self.assertEqual(mb.fmt_bytes(int(3.4 * 1024 * 1024)), "3.4 MB")
        self.assertEqual(mb.fmt_bytes(5 * 1024 ** 4), "5.0 TB")
        self.assertEqual(mb.fmt_elapsed(1.26), "1.3 s")
        self.assertEqual(mb.fmt_elapsed(125), "2 min 5 s")

    def test_progress_events_make_no_lines(self):
        self.assertEqual(mb.format_event("file", {}), [])
        self.assertEqual(mb.format_event("status", {}), [])

    def test_row_plan_and_done_lines(self):
        plan = mb.Plan(src="S", dst="D", row=1, src_files=3, src_dirs=2, unchanged=1,
                       copies=[("a", 1024), ("b", 512)], copy_bytes=1536, mkdirs=["m"],
                       delete_files=["x"], delete_dirs=[], suppressed_deletes=0)
        lines = mb.format_event("row_plan", {"row": 1, "plan": plan})
        self.assertEqual(len(lines), 1)
        text, tag = lines[0]
        self.assertIn("3 files in 2 folders: 1 unchanged, 2 files to copy (1.5 KB), 1 folder to create, "
                      "1 file + 0 folders to delete", text)
        self.assertEqual(tag, "plain")
        plan.suppressed_deletes = 7
        plan.errors = ["cannot list S\\locked: Access is denied"]
        lines = mb.format_event("row_plan", {"row": 1, "plan": plan})
        self.assertEqual([tag for _, tag in lines], ["plain", "warn", "error"])
        self.assertIn("7 extra items in TO are kept", lines[1][0])

        result = mb.RowResult(copied=2, copied_bytes=1536, made_dirs=1, deleted_files=1, elapsed=0.5)
        (text, tag), = mb.format_event("row_done", {"row": 1, "result": result, "plan": plan})
        self.assertIn("copied 2 files (1.5 KB), created 1 folder, deleted 1 file + 0 folders, 0 errors — 0.5 s", text)
        self.assertEqual(tag, "plain")
        result.errors = [f"cannot copy f{i}" for i in range(mb.MAX_ERROR_LINES + 3)]
        lines = mb.format_event("row_done", {"row": 1, "result": result, "plan": plan})
        self.assertEqual(lines[0][1], "error")
        self.assertEqual(len(lines), 1 + mb.MAX_ERROR_LINES + 1, "capped, with an '… and N more' line")
        self.assertIn("and 3 more errors", lines[-1][0])

    def test_summary_lines(self):
        s = mb.Summary(rows=2, rows_ok=2, copied=5, copied_bytes=2048, elapsed=3)
        (text, tag), = mb.format_event("summary", {"summary": s, "plans": []})
        self.assertEqual(tag, "ok")
        self.assertIn("Done: 2 of 2 rows OK — copied 5 files (2.0 KB), deleted 0 files + 0 folders, 0 errors", text)
        s.errors = 1
        (_, tag), = mb.format_event("summary", {"summary": s, "plans": []})
        self.assertEqual(tag, "error")
        s = mb.Summary(rows=1, cancelled=True)
        (_, tag), = mb.format_event("summary", {"summary": s, "plans": []})
        self.assertEqual(tag, "warn", "a clean cancel is a warning, not an error")

    def test_deletion_summary_lists_only_rows_that_delete_and_samples(self):
        quiet = mb.Plan(src="S1", dst="D1", row=1)
        loud = mb.Plan(src="S2", dst="D2", row=2, delete_files=[f"f{i}" for i in range(6)], delete_dirs=["old"])
        text = mb.deletion_summary([quiet, loud])
        self.assertNotIn("Row 1", text)
        self.assertIn("Row 2  ->  D2:  6 files, 1 folder", text)
        self.assertIn("        f0", text)
        self.assertIn("… and 3 more", text)
        self.assertTrue(text.endswith("Continue?"))

    def test_import_builds_no_tk_root(self):
        import tkinter
        self.assertIsNone(getattr(tkinter, "_default_root", None))
        self.assertTrue(callable(mb.App))


if __name__ == "__main__":
    unittest.main()
