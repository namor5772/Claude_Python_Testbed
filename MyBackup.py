"""MyBackup — mirror a table of FROM directories into TO directories.

The backup "setup" is a two-column table, FROM and TO: each row names a
directory to back up (its whole subtree) and the directory that must end up
as an exact copy of it. [BACKUP] mirrors every row: new and changed files are
copied (size + modification time decide, so a repeat run copies only what
changed), missing directories are created, and anything in TO that no longer
exists in FROM is deleted — afterwards TO's contents and structure are
identical to FROM's, relative to their roots. The GUI follows CSVEditor: a
toolbar, a Treeview with inline editing, Open / Save / Save As of the setup
file, and per-user state outside the repo.

Persistence: the setup is a plain CSV (header FROM,TO) that lives, like the
window state, under ~/.config/mybackup/ — per MACHINE, deliberately, because
the paths are machine-specific (D:\\ on the desktop, /Volumes/... on the Mac).
Open / Save As let you keep named setups anywhere; the state remembers which
one is current, and [BACKUP] saves the table before it runs, so a later
``python MyBackup.py --headless`` mirrors exactly the layout last backed up.

Safety rules a row must pass before anything is touched: FROM exists; TO is
not FROM, not inside FROM, and FROM is not inside TO; TO is not a drive /
filesystem root or the home directory (a mirror deletes everything else
there); TO's PARENT exists — only the last path component is created, so an
unmounted backup disk gets an error instead of a copy onto the system disk
(the /Volumes trap); and no two rows share or nest a TO (the second mirror
would delete the first's copy). Symlinks and junctions are never followed.
If the FROM scan was incomplete (a folder it could not list), the row is
copied but nothing is deleted from its TO — a transient permission problem
must not erase the backup copy. The GUI asks once, default No, before a run
that would delete anything; headless runs do not ask.
"""

import argparse
import csv
import json
import os
import queue
import shutil
import stat
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "MyBackup"
HEADERS = ["FROM", "TO"]
# Per-user, per-machine state and the default setup file, outside the repo —
# the same convention as CSVEditor's ~/.config/csveditor.
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".config", "mybackup")
STATE_FILE = os.path.join(CONFIG_DIR, "state.json")
DEFAULT_SETUP_FILE = os.path.join(CONFIG_DIR, "backup_setup.csv")
LOG_FILE = os.path.join(CONFIG_DIR, "mybackup.log")
LOG_MAX_BYTES = 1_000_000  # one-slot rotation to mybackup.log.1, like the suite's other runtime logs
MTIME_TOLERANCE = 2.0  # seconds: FAT32 keeps modification times to 2 s (exFAT to 10 ms, NTFS to 100 ns)
MAX_ERROR_LINES = 50  # per row, in the log pane and file — the error COUNT is always exact

_isjunction = getattr(os.path, "isjunction", lambda path: False)  # Python 3.12+


# ── Setup file (the FROM / TO table) ──────────────────────────────────

def load_setup(path):
    """The FROM / TO rows of a setup file: a CSV whose first row is the FROM,TO
    header (optional — a headerless two-column file loads too). Every row is
    padded or cut to the two columns; nothing is normalised here, the run does
    that, so the table shows exactly what the file holds."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        lines = list(csv.reader(f))
    if lines and [c.strip().upper() for c in lines[0][:2]] == HEADERS:
        lines = lines[1:]
    return [(list(row) + ["", ""])[:2] for row in lines]


def save_setup(path, rows):
    """Write rows as FROM,TO CSV (UTF-8, CRLF like CSVEditor's saves) through a
    temp file + os.replace, so a crash mid-write cannot leave a truncated
    setup behind."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADERS)
        writer.writerows((list(row) + ["", ""])[:2] for row in rows)
    os.replace(tmp, path)


def resolve_setup_path(explicit=None):
    """The setup file a run should use: an explicit --setup path, else the
    file the GUI last had open (state.json), else the default — or None when
    none of them exists."""
    if explicit:
        return explicit if os.path.isfile(explicit) else None
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            last = json.load(f).get("filepath")
    except (OSError, json.JSONDecodeError, AttributeError):
        last = None
    if last and os.path.isfile(last):
        return last
    return DEFAULT_SETUP_FILE if os.path.isfile(DEFAULT_SETUP_FILE) else None


# ── Row validation ────────────────────────────────────────────────────

def normalize_dir(text):
    """A table cell as an absolute, normalised directory path ('' for blank).
    Trailing separators and surrounding quotes (a pasted "C:\\path") go; ~ is
    expanded."""
    text = (text or "").strip().strip('"')
    if not text:
        return ""
    return os.path.normpath(os.path.abspath(os.path.expanduser(text)))


def _key(path):
    return os.path.normcase(path)


def is_filesystem_root(path):
    """True for C:\\, / and a UNC share root — a path that is its own parent."""
    return os.path.dirname(path) == path


def is_within(child, parent):
    """True when child IS parent or lies inside it — compared case-folded, the
    way the Windows filesystem does, and component-wise, so C:\\ab is not
    inside C:\\a."""
    child, parent = _key(child), _key(parent)
    if child == parent:
        return True
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:  # different drives
        return False


def validate_row(src, dst):
    """The reason this (normalised) FROM / TO pair must not run, or None."""
    if not src:
        return "FROM is empty"
    if not dst:
        return "TO is empty"
    if not os.path.isdir(src):
        return f"FROM is not an existing directory: {src}"
    if _key(src) == _key(dst):
        return "TO is the same directory as FROM"
    # The two "would delete everything else" rules come before the nesting
    # rules: a root or home TO usually contains FROM too, and the clearer
    # reason should be the one reported.
    if is_filesystem_root(dst):
        return f"TO is a drive / filesystem root — a mirror would delete everything else on it: {dst}"
    if _key(dst) == _key(normalize_dir("~")):
        return f"TO is the home directory — a mirror would delete everything else in it: {dst}"
    if is_within(dst, src):
        return f"TO is inside FROM: {dst}"
    if is_within(src, dst):
        return f"FROM is inside TO: {src}"
    if os.path.exists(dst) and not os.path.isdir(dst):
        return f"TO exists but is not a directory: {dst}"
    parent = os.path.dirname(dst)
    if not os.path.isdir(parent):
        return f"TO's parent folder does not exist (backup drive not connected?): {parent}"
    return None


def validate_rows(rows):
    """Split the table into runnable (row_no, src, dst) triples and
    (row_no, reason) errors, row numbers being 1-based table positions. Blank
    rows are skipped silently. Besides each row's own rules, no two rows may
    share a TO or nest one TO inside another — the later mirror would delete
    what the earlier one copied — so both rows of such a pair are refused."""
    active, errors = [], []
    for row_no, row in enumerate(rows, 1):
        cells = list(row) + ["", ""]
        src, dst = normalize_dir(cells[0]), normalize_dir(cells[1])
        if not src and not dst:
            continue
        reason = validate_row(src, dst)
        if reason:
            errors.append((row_no, reason))
        else:
            active.append((row_no, src, dst))
    clashing = set()
    for a in range(len(active)):
        for b in range(a + 1, len(active)):
            if is_within(active[a][2], active[b][2]) or is_within(active[b][2], active[a][2]):
                errors.append((active[a][0], f"TO clashes with row {active[b][0]}'s TO: {active[b][2]}"))
                errors.append((active[b][0], f"TO clashes with row {active[a][0]}'s TO: {active[a][2]}"))
                clashing.update((a, b))
    active = [triple for i, triple in enumerate(active) if i not in clashing]
    errors.sort(key=lambda pair: pair[0])
    return active, errors


# ── Mirror engine (pure: no Tk, no globals) ──────────────────────────

@dataclass
class Plan:
    """Everything a mirror of src into dst will do, decided before anything is
    touched. Relative paths throughout; delete_dirs is deepest-first and mkdirs
    shallowest-first so each can be applied in order."""
    src: str
    dst: str
    row: int = 0
    src_files: int = 0
    src_dirs: int = 0
    copies: list = field(default_factory=list)  # (rel, size)
    copy_bytes: int = 0
    unchanged: int = 0
    mkdirs: list = field(default_factory=list)
    delete_files: list = field(default_factory=list)
    delete_dirs: list = field(default_factory=list)
    suppressed_deletes: int = 0  # extras left alone because the FROM scan was incomplete
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)  # scan errors

    @property
    def deletions(self):
        return len(self.delete_files) + len(self.delete_dirs)


@dataclass
class RowResult:
    copied: int = 0
    copied_bytes: int = 0
    made_dirs: int = 0
    deleted_files: int = 0
    deleted_dirs: int = 0
    errors: list = field(default_factory=list)
    cancelled: bool = False
    elapsed: float = 0.0


@dataclass
class Summary:
    rows: int = 0
    rows_ok: int = 0
    rows_failed: int = 0
    copied: int = 0
    copied_bytes: int = 0
    deleted_files: int = 0
    deleted_dirs: int = 0
    errors: int = 0
    cancelled: bool = False
    dry_run: bool = False
    elapsed: float = 0.0

    @property
    def ok(self):
        return not (self.rows_failed or self.errors or self.cancelled)


@dataclass
class TreeScan:
    """One directory tree, as relative paths: dirs (a set), files
    ({rel: (size, mtime)}), links (symlinks and Windows junctions, never
    followed), specials (sockets, devices — neither copied nor removed) and
    the folders that could not be listed (errors)."""
    dirs: set = field(default_factory=set)
    files: dict = field(default_factory=dict)
    links: list = field(default_factory=list)
    specials: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def scan_tree(root):
    """Walk root without following links. A link is recorded, not entered: a
    mirror never recurses into one or copies its target, and a link in TO is
    an extra entry to remove. A folder that cannot be listed is an error,
    not a warning — the caller must know the picture is incomplete before
    it deletes anything."""
    scan = TreeScan()
    stack = [""]
    while stack:
        rel = stack.pop()
        full = os.path.join(root, rel) if rel else root
        try:
            with os.scandir(full) as it:
                entries = list(it)
        except OSError as exc:
            scan.errors.append(f"cannot list {full}: {exc.strerror or exc}")
            continue
        for entry in entries:
            erel = os.path.join(rel, entry.name) if rel else entry.name
            try:
                if entry.is_symlink() or _isjunction(entry.path):
                    scan.links.append(erel)
                elif entry.is_dir(follow_symlinks=False):
                    scan.dirs.add(erel)
                    stack.append(erel)
                elif entry.is_file(follow_symlinks=False):
                    st = entry.stat(follow_symlinks=False)
                    scan.files[erel] = (st.st_size, st.st_mtime)
                else:
                    scan.specials.append(erel)
            except OSError as exc:
                scan.errors.append(f"cannot read {entry.path}: {exc.strerror or exc}")
    return scan


def _depth(rel):
    return rel.count(os.sep) + rel.count("/")


def plan_mirror(src, dst, row=0):
    """Compare the two trees and decide the work. A file is unchanged when its
    size matches and its modification time agrees within MTIME_TOLERANCE;
    everything else in FROM is copied. Items in TO that FROM lacks are
    deleted — unless the FROM scan hit an error, in which case they are kept
    and counted in suppressed_deletes (a folder we could not list may hold
    exactly the files that an unconditional mirror would then erase)."""
    plan = Plan(src=src, dst=dst, row=row)
    source = scan_tree(src)
    target = scan_tree(dst) if os.path.isdir(dst) else TreeScan()
    plan.errors = source.errors + target.errors
    plan.warnings += [f"skipped link in FROM (links are not copied): {os.path.join(src, rel)}"
                      for rel in sorted(source.links)]
    plan.warnings += [f"skipped special file in FROM: {os.path.join(src, rel)}" for rel in sorted(source.specials)]
    plan.warnings += [f"special file in TO left alone: {os.path.join(dst, rel)}" for rel in sorted(target.specials)]
    plan.src_files, plan.src_dirs = len(source.files), len(source.dirs)
    plan.mkdirs = sorted(source.dirs - target.dirs, key=lambda rel: (_depth(rel), rel))
    for rel in sorted(source.files):
        size, mtime = source.files[rel]
        current = target.files.get(rel)
        if current is None or current[0] != size or abs(current[1] - mtime) > MTIME_TOLERANCE:
            plan.copies.append((rel, size))
            plan.copy_bytes += size
        else:
            plan.unchanged += 1
    # A link in TO is always an extra (links are never mirrored), and it is
    # removed with the files — BEFORE any directory is created, or a stale
    # directory link would survive makedirs(exist_ok=True) and the copies
    # would land through it in whatever it points at.
    extra_files = sorted((set(target.files) - set(source.files)) | set(target.links))
    extra_dirs = sorted(target.dirs - source.dirs, key=lambda rel: (-_depth(rel), rel))
    if source.errors:
        plan.suppressed_deletes = len(extra_files) + len(extra_dirs)
    else:
        plan.delete_files, plan.delete_dirs = extra_files, extra_dirs
    return plan


def _remove_file(path):
    """Remove a file or a link (never a link's target). Windows refuses a
    plain remove on a read-only file and on a link to a directory, which it
    removes with rmdir instead."""
    try:
        os.remove(path)
    except PermissionError:
        if os.path.islink(path) or _isjunction(path):
            os.rmdir(path)
            return
        os.chmod(path, stat.S_IWRITE)
        os.remove(path)


def _remove_dir(path):
    try:
        os.rmdir(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        os.rmdir(path)


def _copy_file(src, dst):
    try:
        shutil.copy2(src, dst)
    except PermissionError:
        if not os.path.isfile(dst):
            raise
        # copy2 keeps the read-only bit, so an earlier copy of a read-only file
        # cannot be overwritten until the bit is cleared.
        os.chmod(dst, stat.S_IWRITE)
        shutil.copy2(src, dst)


def execute_plan(plan, report=None, cancel=None):
    """Apply a Plan: delete the extra files, then the extra directories
    (deepest first — their files are already gone), create the missing
    directories (shallowest first), then copy. Deleting before copying is
    what makes a path that changed kind (file ↔ directory) come out right.
    Errors are recorded per item and the run goes on; cancel (a
    threading.Event) is checked between items."""
    report = report or (lambda kind, **data: None)
    result = RowResult()
    start = time.monotonic()

    def cancelled():
        return cancel is not None and cancel.is_set()

    try:
        os.makedirs(plan.dst, exist_ok=True)
    except OSError as exc:
        result.errors.append(f"cannot create TO {plan.dst}: {exc.strerror or exc}")
        result.elapsed = time.monotonic() - start
        return result

    if plan.delete_files or plan.delete_dirs:
        report("status", row=plan.row,
               text=f"Row {plan.row}: deleting {len(plan.delete_files)} files and "
                    f"{len(plan.delete_dirs)} folders in {plan.dst}…")
    for rel in plan.delete_files:
        if cancelled():
            break
        path = os.path.join(plan.dst, rel)
        try:
            _remove_file(path)
            result.deleted_files += 1
        except OSError as exc:
            result.errors.append(f"cannot delete {path}: {exc.strerror or exc}")
    for rel in plan.delete_dirs:
        if cancelled():
            break
        path = os.path.join(plan.dst, rel)
        try:
            _remove_dir(path)
            result.deleted_dirs += 1
        except OSError as exc:
            result.errors.append(f"cannot delete folder {path}: {exc.strerror or exc}")
    for rel in plan.mkdirs:
        if cancelled():
            break
        path = os.path.join(plan.dst, rel)
        try:
            os.makedirs(path, exist_ok=True)
            result.made_dirs += 1
        except OSError as exc:
            result.errors.append(f"cannot create folder {path}: {exc.strerror or exc}")

    total, done_bytes = len(plan.copies), 0
    for index, (rel, size) in enumerate(plan.copies):
        if cancelled():
            break
        report("file", row=plan.row, index=index, total=total, done_bytes=done_bytes,
               total_bytes=plan.copy_bytes, rel=rel)
        src_path, dst_path = os.path.join(plan.src, rel), os.path.join(plan.dst, rel)
        try:
            _copy_file(src_path, dst_path)
            result.copied += 1
            result.copied_bytes += size
        except OSError as exc:
            result.errors.append(f"cannot copy {src_path}: {exc.strerror or exc}")
        done_bytes += size
    if total and not cancelled():
        report("file", row=plan.row, index=total, total=total, done_bytes=done_bytes,
               total_bytes=plan.copy_bytes, rel="")

    result.cancelled = cancelled()
    result.elapsed = time.monotonic() - start
    return result


def run_rows(rows, report, confirm=None, cancel=None, dry_run=False):
    """Mirror every row of the table. The rows are validated first (a bad row
    is reported and the good ones still run — one unplugged backup drive
    should not stop the others), then ALL rows are planned, then, if the plans
    delete anything and a confirm callable was given, it is asked once with
    the plans; a False answer changes nothing. dry_run stops after planning.
    report(kind, **data) receives every event (see format_event)."""
    start = time.monotonic()
    summary = Summary(dry_run=dry_run)
    active, errors = validate_rows(rows)
    summary.rows = len(active) + len({row_no for row_no, _ in errors})
    for row_no, reason in errors:
        report("row_error", row=row_no, message=reason)
    summary.rows_failed = len({row_no for row_no, _ in errors})

    plans = []
    for row_no, src, dst in active:
        if cancel is not None and cancel.is_set():
            break
        report("row_start", row=row_no, src=src, dst=dst)
        plan = plan_mirror(src, dst, row=row_no)
        plans.append(plan)
        report("row_plan", row=row_no, plan=plan)

    if dry_run:
        summary.elapsed = time.monotonic() - start
        report("summary", summary=summary, plans=plans)
        return summary

    if cancel is not None and cancel.is_set():
        summary.cancelled = True
        summary.elapsed = time.monotonic() - start
        report("cancelled", before_any=True)
        return summary

    if confirm is not None and any(plan.deletions for plan in plans) and not confirm(plans):
        summary.cancelled = True
        summary.elapsed = time.monotonic() - start
        report("cancelled", before_any=True)
        return summary

    for plan in plans:
        if cancel is not None and cancel.is_set():
            summary.cancelled = True
            report("cancelled", before_any=False, row=plan.row)
            break
        result = execute_plan(plan, report=report, cancel=cancel)
        report("row_done", row=plan.row, result=result, plan=plan)
        summary.copied += result.copied
        summary.copied_bytes += result.copied_bytes
        summary.deleted_files += result.deleted_files
        summary.deleted_dirs += result.deleted_dirs
        summary.errors += len(result.errors) + len(plan.errors)
        if result.errors or plan.errors:
            summary.rows_failed += 1
        else:
            summary.rows_ok += 1
        if result.cancelled:
            summary.cancelled = True
            report("cancelled", before_any=False, row=plan.row)
            break
    summary.elapsed = time.monotonic() - start
    report("summary", summary=summary, plans=plans)
    return summary


# ── Reporting (shared by the GUI's log pane, the console and the log file) ──

def fmt_bytes(n):
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{n:,} B" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024


def fmt_elapsed(seconds):
    if seconds < 60:
        return f"{seconds:.1f} s"
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes)} min {rest:.0f} s"


def _plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def format_event(kind, data):
    """The log lines for one run event, as (text, tag) pairs — tag is one of
    head / row / warn / error / ok / plain. Progress events ('file', 'status')
    produce none: they drive the progress bar and are not worth a line."""
    lines = []
    if kind == "run_start":
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dry = "  — DRY RUN, nothing will change" if data.get("dry_run") else ""
        lines.append((f"=== {APP_TITLE} {stamp}  {data['setup']}  ({_plural(data['rows'], 'row')}){dry}", "head"))
    elif kind == "row_error":
        lines.append((f"Row {data['row']}: ERROR — {data['message']}", "error"))
    elif kind == "row_start":
        lines.append((f"Row {data['row']}: {data['src']}  ->  {data['dst']}", "row"))
    elif kind == "row_plan":
        plan = data["plan"]
        text = (f"    {_plural(plan.src_files, 'file')} in {_plural(plan.src_dirs, 'folder')}: "
                f"{plan.unchanged:,} unchanged, {_plural(len(plan.copies), 'file')} to copy "
                f"({fmt_bytes(plan.copy_bytes)}), {_plural(len(plan.mkdirs), 'folder')} to create, "
                f"{_plural(len(plan.delete_files), 'file')} + {_plural(len(plan.delete_dirs), 'folder')} to delete")
        lines.append((text, "plain"))
        if plan.suppressed_deletes:
            lines.append((f"    WARNING — the FROM scan was incomplete, so {plan.suppressed_deletes:,} "
                          f"extra items in TO are kept, not deleted", "warn"))
        for warning in plan.warnings[:MAX_ERROR_LINES]:
            lines.append((f"    warning: {warning}", "warn"))
        if len(plan.warnings) > MAX_ERROR_LINES:
            lines.append((f"    … and {len(plan.warnings) - MAX_ERROR_LINES:,} more warnings", "warn"))
        for error in plan.errors[:MAX_ERROR_LINES]:
            lines.append((f"    ERROR: {error}", "error"))
        if len(plan.errors) > MAX_ERROR_LINES:
            lines.append((f"    … and {len(plan.errors) - MAX_ERROR_LINES:,} more errors", "error"))
    elif kind == "row_done":
        result = data["result"]
        # Every row is planned before any is copied, so this line can land
        # several lines below its row's header — it names the row itself.
        text = (f"Row {data['row']} done: copied {_plural(result.copied, 'file')} ({fmt_bytes(result.copied_bytes)}), "
                f"created {_plural(result.made_dirs, 'folder')}, deleted {_plural(result.deleted_files, 'file')} + "
                f"{_plural(result.deleted_dirs, 'folder')}, {_plural(len(result.errors), 'error')} — "
                f"{fmt_elapsed(result.elapsed)}")
        lines.append((text, "error" if result.errors else "plain"))
        for error in result.errors[:MAX_ERROR_LINES]:
            lines.append((f"    ERROR: {error}", "error"))
        if len(result.errors) > MAX_ERROR_LINES:
            lines.append((f"    … and {len(result.errors) - MAX_ERROR_LINES:,} more errors", "error"))
    elif kind == "cancelled":
        if data.get("before_any"):
            lines.append(("Backup cancelled — nothing was changed.", "warn"))
        else:
            lines.append((f"Backup cancelled during row {data['row']} — that TO is only partly updated; "
                          f"the next run completes it.", "warn"))
    elif kind == "crash":
        lines.append((f"ERROR — the backup stopped: {data['message']}", "error"))
    elif kind == "summary":
        s = data["summary"]
        if s.dry_run:
            plans = data.get("plans", [])
            copies = sum(len(p.copies) for p in plans)
            size = sum(p.copy_bytes for p in plans)
            deletes = sum(p.deletions for p in plans)
            lines.append((f"Dry run: {_plural(len(plans), 'row')} planned, {_plural(s.rows_failed, 'row')} refused — "
                          f"{_plural(copies, 'file')} would be copied ({fmt_bytes(size)}), "
                          f"{_plural(deletes, 'item')} deleted — {fmt_elapsed(s.elapsed)}",
                          "error" if s.rows_failed else "ok"))
        else:
            tag = "ok" if s.ok else ("warn" if s.cancelled and not (s.errors or s.rows_failed) else "error")
            lines.append((f"Done: {s.rows_ok} of {_plural(s.rows, 'row')} OK — copied {_plural(s.copied, 'file')} "
                          f"({fmt_bytes(s.copied_bytes)}), deleted {_plural(s.deleted_files, 'file')} + "
                          f"{_plural(s.deleted_dirs, 'folder')}, {_plural(s.errors, 'error')} — "
                          f"{fmt_elapsed(s.elapsed)}", tag))
    return lines


def deletion_summary(plans, sample=4):
    """The text of the one confirmation the GUI shows before a run that
    deletes: per row, how many files and folders go, and the first few."""
    lines = ["This backup will DELETE items in the TO directories that are no longer in their FROM directories:", ""]
    for plan in plans:
        if not plan.deletions:
            continue
        lines.append(f"Row {plan.row}  ->  {plan.dst}:  {_plural(len(plan.delete_files), 'file')}, "
                     f"{_plural(len(plan.delete_dirs), 'folder')}")
        items = plan.delete_files + plan.delete_dirs
        lines.extend(f"        {rel}" for rel in items[:sample])
        if len(items) > sample:
            lines.append(f"        … and {len(items) - sample:,} more")
        lines.append("")
    lines.append("Deleted items cannot be recovered. Continue?")
    return "\n".join(lines)


class LogFile:
    """Append-only run log with the suite's one-slot size-cap rotation (the
    file is moved to .1 when it passes LOG_MAX_BYTES, replacing the previous
    .1). Every line carries a timestamp; writing is best-effort — a backup
    must not fail because its log cannot be written."""

    def __init__(self, path=None):
        self.path = path or LOG_FILE

    def write(self, lines):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            try:
                if os.path.getsize(self.path) > LOG_MAX_BYTES:
                    os.replace(self.path, self.path + ".1")
            except OSError:
                pass
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.path, "a", encoding="utf-8") as f:
                for text, _tag in lines:
                    f.write(f"{stamp}  {text}\n")
        except OSError:
            pass


# ── Headless ──────────────────────────────────────────────────────────

def run_headless(setup_path, dry_run=False, out=None):
    """Mirror the setup file's rows with no GUI. Exit code 0 when every row
    mirrored cleanly, 1 when a row was refused or an item failed, 2 when
    there is no usable setup. Every log line goes to stdout and the log file;
    nothing asks — a scheduled job has nobody to answer."""
    out = out or sys.stdout
    log = LogFile()
    if not setup_path:
        print(f"{APP_TITLE}: no setup file — open the GUI once and save a FROM / TO table, "
              f"or pass --setup FILE (default: {DEFAULT_SETUP_FILE})", file=out)
        return 2
    try:
        rows = load_setup(setup_path)
    except (OSError, csv.Error) as exc:
        print(f"{APP_TITLE}: cannot read setup {setup_path}: {exc}", file=out)
        return 2
    if not any(cell.strip() for row in rows for cell in row):
        print(f"{APP_TITLE}: the setup {setup_path} has no rows", file=out)
        return 2

    def report(kind, **data):
        lines = format_event(kind, data)
        if lines:
            log.write(lines)
            for text, _tag in lines:
                print(text, file=out)

    report("run_start", setup=setup_path, rows=len(rows), dry_run=dry_run)
    summary = run_rows(rows, report, confirm=None, cancel=None, dry_run=dry_run)
    return 0 if summary.ok else 1


# ── GUI ───────────────────────────────────────────────────────────────

class App:
    def __init__(self, setup_path=None):
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("1000x620")
        self.root.minsize(720, 460)

        self.filepath = None  # the setup file in use; None until one is opened or saved
        self.rows = []  # list of [from, to]
        self.modified = False
        self._col_widths = {}
        self._queue = queue.Queue()
        self._worker = None
        self._cancel = threading.Event()
        self._confirm_event = threading.Event()
        self._confirm_answer = False
        self._close_when_done = False
        self._log_file = LogFile()

        self._build_ui()
        self._load_state(setup_path)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self):
        self.root.mainloop()

    # ── UI ────────────────────────────────────────────────────────────

    def _build_ui(self):
        toolbar = tk.Frame(self.root)
        toolbar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=4)

        tk.Button(toolbar, text="New", command=self._new_setup).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar, text="Open…", command=self._open_file).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar, text="Save", command=self._save_file).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar, text="Save As…", command=self._save_as).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        tk.Button(toolbar, text="Insert Row Above", command=self._insert_row_above).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar, text="Insert Row Below", command=self._insert_row_below).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar, text="Copy Row", command=self._copy_row).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar, text="Delete Row", command=self._delete_row).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        tk.Button(toolbar, text="Browse FROM…", command=lambda: self._browse(0)).pack(side=tk.LEFT, padx=2)
        tk.Button(toolbar, text="Browse TO…", command=lambda: self._browse(1)).pack(side=tk.LEFT, padx=2)

        # Action row: the prominent BACKUP button, Preview (a dry run), Cancel,
        # and the progress bar for the running copy.
        action = tk.Frame(self.root)
        action.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 2))
        self._backup_btn = tk.Button(action, text="BACKUP", font=("Arial", 12, "bold"), width=12,
                                     bg="#8FD68F", activebackground="#7CCB7C",
                                     command=lambda: self._start_run(dry_run=False))
        self._backup_btn.pack(side=tk.LEFT, padx=2, pady=2)
        self._preview_btn = tk.Button(action, text="Preview", command=lambda: self._start_run(dry_run=True))
        self._preview_btn.pack(side=tk.LEFT, padx=2)
        self._cancel_btn = tk.Button(action, text="Cancel", state=tk.DISABLED, command=self._cancel_run)
        self._cancel_btn.pack(side=tk.LEFT, padx=2)
        self._progress = ttk.Progressbar(action, orient=tk.HORIZONTAL, mode="determinate")
        self._progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 4))

        # Status row: the setup file + row count at the right (CSVEditor keeps
        # its status on the toolbar, but eleven buttons leave it no room
        # there), the running copy's progress text at the left.
        status_row = tk.Frame(self.root)
        status_row.pack(side=tk.TOP, fill=tk.X, padx=8, pady=(0, 2))
        self._status_var = tk.StringVar(value="No setup file")
        tk.Label(status_row, textvariable=self._status_var, anchor=tk.E).pack(side=tk.RIGHT, padx=(8, 0))
        self._progress_var = tk.StringVar(value="")
        tk.Label(status_row, textvariable=self._progress_var, anchor=tk.W).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Treeview style — 'clam' so the heading background is respected
        # (CSVEditor's colours, so the two tables look like siblings).
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Backup.Treeview", background="#D6EBFF", fieldbackground="#D6EBFF")
        style.configure("Backup.Treeview.Heading", background="#FFFFB3", foreground="black")
        style.map("Backup.Treeview.Heading", background=[("active", "#FFFF88")])

        paned = ttk.PanedWindow(self.root, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        container = tk.Frame(paned)
        self.tree = ttk.Treeview(container, columns=("from", "to"), show="tree headings",
                                 selectmode="browse", style="Backup.Treeview")
        self.tree.heading("#0", text="#")
        self.tree.column("#0", width=44, minwidth=32, stretch=False, anchor=tk.E)
        for cid, header in zip(("from", "to"), HEADERS):
            self.tree.heading(cid, text=header, anchor=tk.W)
            self.tree.column(cid, width=440, minwidth=80, anchor=tk.W)
        vsb = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.tree.yview)
        hsb = ttk.Scrollbar(container, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)
        self.tree.bind("<Double-1>", self._on_double_click)
        paned.add(container, weight=3)

        # Log pane: what each run planned and did, the same lines as the log file.
        log_frame = tk.Frame(paned)
        self._log = tk.Text(log_frame, height=9, wrap=tk.NONE, state=tk.DISABLED,
                            font=("Consolas", 9) if sys.platform == "win32" else ("Menlo", 11))
        log_vsb = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self._log.yview)
        log_hsb = ttk.Scrollbar(log_frame, orient=tk.HORIZONTAL, command=self._log.xview)
        self._log.configure(yscrollcommand=log_vsb.set, xscrollcommand=log_hsb.set)
        self._log.grid(row=0, column=0, sticky="nsew")
        log_vsb.grid(row=0, column=1, sticky="ns")
        log_hsb.grid(row=1, column=0, sticky="ew")
        log_frame.grid_rowconfigure(0, weight=1)
        log_frame.grid_columnconfigure(0, weight=1)
        self._log.tag_configure("head", font=("Consolas", 9, "bold") if sys.platform == "win32" else ("Menlo", 11, "bold"))
        self._log.tag_configure("row", foreground="#003399")
        self._log.tag_configure("warn", foreground="#B35C00")
        self._log.tag_configure("error", foreground="#B00000")
        self._log.tag_configure("ok", foreground="#1B7F2A")
        paned.add(log_frame, weight=1)

    # ── File operations ───────────────────────────────────────────────

    def _confirm_discard(self, question):
        """CSVEditor's unsaved-changes prompt: Yes saves first, No discards,
        Cancel (None) aborts. Returns False when the caller must stop."""
        if not self.modified:
            return True
        answer = messagebox.askyesnocancel("Unsaved changes", question, parent=self.root)
        if answer is None:
            return False
        if answer:
            self._save_file()
            return not self.modified  # a cancelled Save As leaves it modified
        return True

    def _new_setup(self):
        if not self._confirm_discard("Save the current table before starting a new one?"):
            return
        self.filepath = None
        self.rows = []
        self.modified = False
        self._refresh_tree()
        self._update_status()

    def _open_file(self):
        if not self._confirm_discard("Save the current table before opening another setup?"):
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="Open backup setup",
            initialdir=os.path.dirname(self.filepath) if self.filepath else CONFIG_DIR,
            filetypes=[("Backup setup (CSV)", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        self._load_setup_file(path)

    def _load_setup_file(self, path):
        try:
            rows = load_setup(path)
        except (OSError, csv.Error) as exc:
            messagebox.showerror("Error", f"Failed to read the setup file:\n{exc}", parent=self.root)
            return False
        self.filepath = path
        self.rows = rows
        self.modified = False
        self._refresh_tree()
        self._update_status()
        return True

    def _save_file(self):
        if not self.filepath:
            self._save_as()
            return
        self._write_setup(self.filepath)

    def _save_as(self):
        path = filedialog.asksaveasfilename(
            parent=self.root, title="Save backup setup as", defaultextension=".csv",
            filetypes=[("Backup setup (CSV)", "*.csv"), ("All files", "*.*")],
            initialdir=os.path.dirname(self.filepath) if self.filepath else CONFIG_DIR,
            initialfile=os.path.basename(self.filepath or DEFAULT_SETUP_FILE))
        if not path:
            return
        self._write_setup(path)

    def _write_setup(self, path):
        try:
            save_setup(path, self.rows)
        except OSError as exc:
            messagebox.showerror("Error", f"Failed to save the setup file:\n{exc}", parent=self.root)
            return False
        self.filepath = path
        self.modified = False
        self._update_status()
        return True

    # ── Tree display ──────────────────────────────────────────────────

    def _capture_col_widths(self):
        for cid, header in zip(("from", "to"), HEADERS):
            self._col_widths[header] = int(self.tree.column(cid, "width"))

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for i, row in enumerate(self.rows, 1):
            self.tree.insert("", tk.END, text=str(i), values=(row[0], row[1]))

    def _update_status(self):
        mod = " *" if self.modified else ""
        if self.filepath:
            name = os.path.basename(self.filepath)
            self._status_var.set(f"{name}{mod}  |  {len(self.rows)} rows")
            self.root.title(f"{APP_TITLE} — {name}{mod}")
        else:
            self._status_var.set(f"Unsaved table{mod}  |  {len(self.rows)} rows  |  "
                                 f"BACKUP saves it to {os.path.basename(DEFAULT_SETUP_FILE)}")
            self.root.title(f"{APP_TITLE}{mod}")

    # ── Inline editing ────────────────────────────────────────────────

    def _on_double_click(self, event):
        item = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not item or col not in ("#1", "#2"):
            return
        col_idx = int(col[1:]) - 1
        row_idx = self.tree.index(item)
        bbox = self.tree.bbox(item, col)
        if not bbox:
            return
        x, y, w, h = bbox
        entry = tk.Entry(self.tree, font=("TkDefaultFont",))
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, self.rows[row_idx][col_idx])
        entry.select_range(0, tk.END)
        entry.focus_set()

        def commit(e=None):
            new_val = entry.get()
            if self.rows[row_idx][col_idx] != new_val:
                self.rows[row_idx][col_idx] = new_val
                self.tree.set(item, col, new_val)
                self.modified = True
                self._update_status()
            entry.destroy()

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)
        entry.bind("<Escape>", lambda e: entry.destroy())

    # ── Row operations ────────────────────────────────────────────────

    def _selected_index(self, require=False):
        sel = self.tree.selection()
        if not sel:
            if require:
                messagebox.showinfo("No selection", "Select a row first.", parent=self.root)
            return None
        return self.tree.index(sel[0])

    def _commit_row_edit(self, select_idx):
        self.modified = True
        self._refresh_tree()
        self._select_row(select_idx)
        self._update_status()

    def _select_row(self, idx):
        children = self.tree.get_children()
        if 0 <= idx < len(children):
            self.tree.selection_set(children[idx])
            self.tree.focus(children[idx])
            self.tree.see(children[idx])

    def _insert_row_above(self):
        idx = self._selected_index()
        if idx is None:
            idx = 0
        self.rows.insert(idx, ["", ""])
        self._commit_row_edit(idx)

    def _insert_row_below(self):
        idx = self._selected_index()
        if idx is None:
            idx = len(self.rows) - 1
        self.rows.insert(idx + 1, ["", ""])
        self._commit_row_edit(idx + 1)

    def _copy_row(self):
        idx = self._selected_index(require=True)
        if idx is None:
            return
        self.rows.insert(idx + 1, list(self.rows[idx]))
        self._commit_row_edit(idx + 1)

    def _delete_row(self):
        idx = self._selected_index(require=True)
        if idx is None:
            return
        self.rows.pop(idx)
        self.modified = True
        self._refresh_tree()
        if self.rows:
            self._select_row(min(idx, len(self.rows) - 1))
        self._update_status()

    def _browse(self, col):
        """Fill the selected row's FROM (col 0) or TO (col 1) from a directory
        chooser; with nothing selected a new row is appended first."""
        idx = self._selected_index()
        if idx is None:
            self.rows.append(["", ""])
            idx = len(self.rows) - 1
        current = normalize_dir(self.rows[idx][col])
        if current and os.path.isdir(current):
            initial = current
        elif current and os.path.isdir(os.path.dirname(current)):
            initial = os.path.dirname(current)
        else:
            initial = os.path.expanduser("~")
        path = filedialog.askdirectory(parent=self.root, title=f"Choose the {HEADERS[col]} directory",
                                       initialdir=initial, mustexist=(col == 0))
        if not path:
            self._refresh_tree()
            self._select_row(idx)
            return
        self.rows[idx][col] = os.path.normpath(path)
        self._commit_row_edit(idx)

    # ── Backup run ────────────────────────────────────────────────────

    def _running(self):
        return self._worker is not None and self._worker.is_alive()

    def _persist_for_run(self):
        """[BACKUP] saves the table first — to the current setup file, or to
        the default one when none is open — and the state, so a later headless
        run mirrors exactly this layout. False when the save failed: a backup
        whose layout cannot be recorded is not reproducible, so it does not
        start."""
        path = self.filepath or DEFAULT_SETUP_FILE
        if self.modified or not self.filepath or not os.path.isfile(path):
            if not self._write_setup(path):
                return False
        self._save_state()
        return True

    def _start_run(self, dry_run):
        if self._running():
            return
        rows = [list(row) for row in self.rows]
        if not any(cell.strip() for row in rows for cell in row):
            messagebox.showinfo(APP_TITLE, "The table is empty — add a FROM / TO row first.", parent=self.root)
            return
        if not dry_run and not self._persist_for_run():
            return
        setup_name = self.filepath or "(unsaved table)"
        self._cancel = threading.Event()
        self._confirm_event.clear()
        self._set_running(True)
        self._progress.configure(mode="indeterminate")
        self._progress.start(12)
        self._progress_var.set("Scanning…")
        cancel = self._cancel

        def report(kind, **data):
            lines = format_event(kind, data)
            if lines:
                self._log_file.write(lines)
            self._queue.put((kind, data, lines))

        def confirm(plans):
            self._queue.put(("confirm", {"text": deletion_summary(plans)}, []))
            self._confirm_event.wait()
            return self._confirm_answer

        def work():
            try:
                report("run_start", setup=setup_name, rows=len(rows), dry_run=dry_run)
                run_rows(rows, report, confirm=None if dry_run else confirm, cancel=cancel, dry_run=dry_run)
            except Exception as exc:  # a bug must surface in the log, not vanish with the thread
                report("crash", message=f"{type(exc).__name__}: {exc}")
            finally:
                self._queue.put(("done", {}, []))

        self._worker = threading.Thread(target=work, name="MyBackup-run", daemon=True)
        self._worker.start()
        self.root.after(100, self._poll_queue)

    def _cancel_run(self):
        if self._running():
            self._cancel.set()
            self._progress_var.set("Stopping after the current file…")
            self._cancel_btn.config(state=tk.DISABLED)

    def _set_running(self, running):
        state = tk.DISABLED if running else tk.NORMAL
        self._backup_btn.config(state=state)
        self._preview_btn.config(state=state)
        self._cancel_btn.config(state=tk.NORMAL if running else tk.DISABLED)

    def _poll_queue(self):
        """The one place worker events become widgets — the worker never
        touches Tk. The confirm request blocks here in a modal dialog while
        the worker waits on the event for its answer."""
        done = False
        try:
            while True:
                kind, data, lines = self._queue.get_nowait()
                if kind == "done":
                    done = True
                elif kind == "confirm":
                    self._confirm_answer = messagebox.askyesno(
                        APP_TITLE, data["text"], icon="warning", default=messagebox.NO, parent=self.root)
                    self._confirm_event.set()
                elif kind == "file":
                    if str(self._progress.cget("mode")) != "determinate":
                        self._progress.stop()
                        self._progress.configure(mode="determinate")
                    self._progress.configure(maximum=max(data["total_bytes"], 1), value=data["done_bytes"])
                    if data["index"] < data["total"]:
                        self._progress_var.set(f"Row {data['row']}: copying file {data['index'] + 1:,} "
                                               f"of {data['total']:,}  —  {data['rel']}")
                elif kind == "status":
                    self._progress_var.set(data["text"])
                else:
                    for text, tag in lines:
                        self._log_append(text, tag)
        except queue.Empty:
            pass
        if done:
            self._progress.stop()
            self._progress.configure(mode="determinate", value=0)
            self._progress_var.set("")
            self._set_running(False)
            if self._close_when_done:
                self._finish_close()
            return
        self.root.after(100, self._poll_queue)

    def _log_append(self, text, tag):
        self._log.config(state=tk.NORMAL)
        if tag == "head" and self._log.index("end-1c") != "1.0":
            self._log.insert(tk.END, "\n")
        self._log.insert(tk.END, text + "\n", tag)
        self._log.see(tk.END)
        self._log.config(state=tk.DISABLED)

    # ── State persistence ─────────────────────────────────────────────

    def _load_state(self, setup_path=None):
        state = {}
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
        if not isinstance(state, dict):
            state = {}
        geo = state.get("geometry")
        if geo:
            self.root.geometry(geo)
        widths = state.get("col_widths")
        if isinstance(widths, dict):
            for cid, header in zip(("from", "to"), HEADERS):
                if isinstance(widths.get(header), int) and widths[header] > 0:
                    self.tree.column(cid, width=widths[header])
        path = setup_path or state.get("filepath")
        if not (path and os.path.isfile(path)) and os.path.isfile(DEFAULT_SETUP_FILE):
            path = DEFAULT_SETUP_FILE
        if path and os.path.isfile(path):
            self._load_setup_file(path)
        else:
            self._update_status()

    def _save_state(self):
        self._capture_col_widths()
        state = {
            "geometry": self.root.geometry(),
            "filepath": self.filepath,
            "col_widths": self._col_widths,
        }
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except OSError:
            pass

    # ── Close ─────────────────────────────────────────────────────────

    def _on_close(self):
        if self._running():
            if not messagebox.askyesno(APP_TITLE, "A backup is running. Stop it and close?",
                                       default=messagebox.NO, parent=self.root):
                return
            self._cancel.set()
            self._confirm_answer = False
            self._confirm_event.set()
            self._close_when_done = True
            self._progress_var.set("Stopping…")
            return
        if not self._confirm_discard("Save the table before closing?"):
            return
        self._finish_close()

    def _finish_close(self):
        self._save_state()
        self.root.destroy()


# ── Entry point ───────────────────────────────────────────────────────

def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="MyBackup.py",
        description="Mirror a table of FROM directories into TO directories (GUI by default).")
    parser.add_argument("--headless", action="store_true",
                        help="run the backup with no GUI and exit: 0 = every row mirrored, "
                             "1 = a row was refused or an item failed, 2 = no usable setup")
    parser.add_argument("--dry-run", action="store_true",
                        help="headless: print what a backup would copy and delete, change nothing")
    parser.add_argument("--setup", metavar="FILE",
                        help="the FROM,TO setup file to use (default: the one last open in the GUI, "
                             f"else {DEFAULT_SETUP_FILE})")
    args = parser.parse_args(argv)
    if args.headless or args.dry_run:
        return run_headless(resolve_setup_path(args.setup), dry_run=args.dry_run)
    App(setup_path=args.setup).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
