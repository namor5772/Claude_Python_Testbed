"""FileMixin — native file tools: read_file / write_file / edit_file /
glob_files / grep_files (Claude-Code-style contracts).

Why native tools instead of run_command: PowerShell round-trips for file
editing are a quoting/encoding minefield (PS 5.1 Set-Content defaults to
the ANSI codepage, here-strings mangle $ and backticks, CRLF is invisible
in tool output). These tools give the model contracts that FAIL LOUDLY
instead of corrupting files:

- edit_file requires an exact, UNIQUE old_string match — zero matches, or
  more than one without replace_all, is an error the model can retry, never
  a guess applied to the wrong place.
- edit_file (and write_file over an existing file) require the file to have
  been read via read_file first this process (self._file_read_paths), so
  edits are grounded in the file's actual content, not a stale imagination
  of it.
- CRLF line endings and a UTF-8 BOM survive round-trips byte-exactly:
  files are read/written with newline='' so \r\n stays literal in the
  string, and _apply_edit falls back to a \n→\r\n expanded match when the
  model supplies LF-normalized old_string against a CRLF file.
- glob_files / grep_files prune WHILE they walk (FILE_SKIP_DIRS, and the
  guarded roots a wildcard must never wander into — cloud-synced / network
  trees, and on macOS other apps' sandbox containers; name one as `path` to
  search it) and stop at STOP or a 60 s ceiling with PARTIAL results:
  glob.glob could only be filtered after crawling everything, and a ** from
  ~ crawled the whole OneDrive through the macOS File Provider.

All helpers are prefixed _file_* or are unique to this mixin (no MRO
shadowing risk); the pure cores (_file_apply_edit, _file_numbered) are
static for direct unit testing in tests/test_file_mixin.py.
"""
import fnmatch
import glob
import os
import re
import shutil
import sys
import tempfile
import time


# Display / size caps. Line + read caps mirror Claude Code's Read tool scale;
# grep caps keep a repo-wide search from flooding the context window.
FILE_MAX_LINE_CHARS = 500
FILE_DEFAULT_READ_LIMIT = 1000
FILE_MAX_READ_CHARS = 80_000
FILE_GREP_MAX_FILE_BYTES = 2_000_000

# Directory names pruned from glob/grep walks. Includes .claude because its
# worktrees/ subtree holds full repo copies that would double every hit.
FILE_SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", ".claude", "dist", "build",
}

# Seconds a glob / grep walk may run before it returns what it has found so
# far, marked PARTIAL. A walk over local disk finishes in seconds; one that
# runs this long is wandering (a ** from ~) or waiting on a filesystem.
FILE_WALK_TIMEOUT = 60.0


class FileMixin:
    """Native file tools with read-before-edit tracking."""

    # ── shared state ────────────────────────────────────────────────────

    @staticmethod
    def _file_key(path):
        return os.path.normcase(os.path.abspath(path))

    def _file_reads(self):
        # Lazy init so the mixin needs no App.__init__ wiring. Per RUN, not per
        # process: _start_agent clears it, since a GUI session runs several
        # instructions and a later run's model must read a file itself.
        s = getattr(self, "_file_read_paths", None)
        if s is None:
            s = self._file_read_paths = set()
        return s

    # ── pure cores (unit-tested directly) ───────────────────────────────

    @staticmethod
    def _file_path(inp):
        """The tool input's path with ~ expanded (write_file created a literal
        "~" folder in the working directory; read / edit failed on it)."""
        return os.path.expanduser((inp or {}).get("path") or "")

    @staticmethod
    def _file_write_atomic(path, text):
        """Write `text` (UTF-8, newline='' so \r\n stays literal) to a temp
        file beside `path`, then os.replace it into place: the existing file
        is never truncated before the new text is safely on disk. (A plain
        open("w") emptied it first, and a write that then failed — content
        not a string, a lone surrogate — left the user's file at 0 bytes.)
        Writes through a symlink to its target and keeps a POSIX file mode.
        Returns None, or the error text."""
        target = os.path.realpath(path)
        directory = os.path.dirname(target)
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp",
                                       prefix=os.path.basename(target) + ".")
        except OSError as e:
            return str(e)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            if os.path.exists(target):
                try:
                    shutil.copymode(target, tmp)
                except OSError:
                    pass
            os.replace(tmp, target)
            return None
        except (OSError, ValueError) as e:     # ValueError: a lone surrogate
            try:
                os.remove(tmp)
            except OSError:
                pass
            return str(e)

    @staticmethod
    def _file_count(content, sub):
        """Occurrences of `sub` in `content`, OVERLAPPING ones included —
        "end\nend" occurs twice in "end\nend\nend", which str.count calls
        once, so edit_file took the first and called it unique."""
        count, start = 0, 0
        while True:
            i = content.find(sub, start)
            if i < 0:
                return count
            count, start = count + 1, i + 1

    @staticmethod
    def _file_read_text(path, errors="strict"):
        # newline='' keeps \r\n literal in the returned string so writes can
        # round-trip the file's real line endings byte-exactly.
        with open(path, "r", encoding="utf-8", errors=errors, newline="") as f:
            return f.read()

    @staticmethod
    def _file_numbered(text, offset=1, limit=FILE_DEFAULT_READ_LIMIT):
        """cat -n style numbering of a line window. Returns (body, total_lines,
        first_shown, last_shown) — empty body when the window is off the end."""
        lines = text.splitlines()
        total = len(lines)
        offset = max(1, int(offset))
        limit = max(1, int(limit))
        window = lines[offset - 1:offset - 1 + limit]
        out, used = [], 0
        for i, ln in enumerate(window, start=offset):
            if len(ln) > FILE_MAX_LINE_CHARS:
                ln = ln[:FILE_MAX_LINE_CHARS] + " …[line truncated]"
            entry = f"{i:>6}\t{ln}"
            # The size cap ends the window at a WHOLE line, and `last` says
            # which: the header claimed the full window after a cut, and the
            # "re-read with offset=" note then skipped every line past it.
            if out and used + len(entry) + 1 > FILE_MAX_READ_CHARS:
                break
            out.append(entry)
            used += len(entry) + 1
        body = "\n".join(out)
        last = offset + len(out) - 1 if out else 0
        return body, total, (offset if out else 0), last

    @staticmethod
    def _file_apply_edit(content, old, new, replace_all=False):
        """Exact-match string replacement. Returns (new_content, count, error).

        Pure — no IO. The CRLF fallback fires only when the literal match
        fails, the file uses \r\n, and old_string contains no \r itself:
        the model almost certainly supplied LF-normalized text, so both old
        and new are expanded to \r\n to preserve the file's line endings.
        """
        if not old:
            return content, 0, "old_string is empty — provide the exact text to replace."
        if old == new:
            return content, 0, "old_string and new_string are identical — nothing to change."
        o, n = old, new
        count = FileMixin._file_count(content, o)
        if count == 0 and "\r\n" in content and "\r" not in o:
            o = old.replace("\n", "\r\n")
            n = new.replace("\n", "\r\n")
            count = FileMixin._file_count(content, o)
        elif (count and "\r" not in new and "\n" in new and "\r\n" in content
              and content.count("\n") == content.count("\r\n")):
            # A literal match in a pure-CRLF file: new_string's own line
            # breaks must be CRLF too, or one edit leaves mixed endings.
            n = new.replace("\n", "\r\n")
        if count == 0:
            return content, 0, ("old_string not found in the file. It must match the "
                                "current file content EXACTLY, including whitespace and "
                                "indentation. read_file the relevant section and retry.")
        if count > 1 and not replace_all:
            return content, 0, (f"old_string appears {count} times in the file — include "
                                "more surrounding lines to make it unique, or set "
                                "replace_all=true to replace every occurrence.")
        if replace_all:
            return content.replace(o, n), content.count(o), None
        return content.replace(o, n, 1), 1, None

    # ── walk pruning (glob_files / grep_files) ───────────────────────────
    # 2026-10-04: a ** glob whose base reached ~/Library/CloudStorage crawled
    # the whole OneDrive through the macOS File Provider — every never-listed
    # cloud folder enumerated from the server, each stat waiting seconds — and
    # the run looked hung for 50 minutes with STOP powerless: glob.glob can
    # only be FILTERED after it has crawled everything, and nothing inside it
    # read the stop flag. The walk is done by hand now: FILE_SKIP_DIRS and the
    # remote roots are pruned as it descends, and STOP / a time ceiling are
    # honoured between directories.
    # 2026-10-06: the same rule keeps a walk out of other apps' sandbox
    # containers on macOS — a stat inside one is no wait, but a dialog.

    @staticmethod
    def _file_remote_roots():
        """Trees a walk never ENTERS from outside: cloud-synced / network
        ones, where a stat may wait on a server, and on macOS other apps'
        sandbox containers (~/Library/Containers, ~/Library/Group Containers
        — Excel's, OneDrive's, Voice Memos'…), where since macOS 15 one stat
        raises the "would like to access data from other apps" dialog, a
        per-process consent macOS never remembers. Naming one as `path` (or
        a folder below it) searches it normally — the rule is about
        wandering in, not about working there. Normalised absolute paths,
        computed per call so a test can point HOME / the OneDrive variables
        elsewhere."""
        roots = []
        home = os.path.expanduser("~")
        if sys.platform == "darwin":
            roots += [
                # every File Provider domain: OneDrive, Google Drive, Dropbox, Box
                os.path.join(home, "Library", "CloudStorage"),
                os.path.join(home, "Library", "Mobile Documents"),   # iCloud Drive
                "/Volumes",                     # mounted volumes, network shares among them
                # sandboxed apps' private data — TCC's "App Data" protection
                os.path.join(home, "Library", "Containers"),
                os.path.join(home, "Library", "Group Containers"),
            ]
        elif sys.platform == "win32":
            roots += [v for v in (os.environ.get(k) for k in
                                  ("OneDrive", "OneDriveConsumer", "OneDriveCommercial")) if v]
        return {os.path.normcase(os.path.abspath(r)) for r in roots}

    @staticmethod
    def _file_walk_deadline():
        return time.monotonic() + FILE_WALK_TIMEOUT

    def _file_walk_state(self):
        """The bookkeeping one walk carries — its deadline and pruning rules,
        plus what it had to skip or cut short, which _file_walk_notes renders
        for the model and _file_walk_announce for the output pane."""
        return {
            "deadline": self._file_walk_deadline(),
            "remote_roots": self._file_remote_roots(),
            "remote_hits": [],       # remote roots reached and not entered
            "timed_out_at": None,    # the directory the ceiling struck at
            "stopped": False,        # STOP pressed mid-walk
            "started": time.monotonic(),
        }

    def _file_walk_continues(self, state, dirpath):
        """Asked before each directory is listed: False once STOP or the
        ceiling ends the walk (and says which in `state`)."""
        if getattr(self, "stop_requested", False):
            state["stopped"] = True
            return False
        if time.monotonic() > state["deadline"]:
            state["timed_out_at"] = dirpath
            return False
        return True

    @staticmethod
    def _file_dir_enterable(parent, name, state):
        """Whether a walk may descend from `parent` into its subdirectory
        `name`: not a pruned name, not a remote root (recorded), not a
        symlink — a wildcard never follows one; name the target instead."""
        if name in FILE_SKIP_DIRS:
            return False
        path = os.path.join(parent, name)
        if os.path.normcase(os.path.abspath(path)) in state["remote_roots"]:
            if path not in state["remote_hits"]:
                state["remote_hits"].append(path)
            return False
        return not os.path.islink(path)

    @staticmethod
    def _file_entry_is_dir(entry):
        try:
            return entry.is_dir(follow_symlinks=False)
        except OSError:
            return False

    @staticmethod
    def _file_entry_is_file(entry):
        try:
            return entry.is_file()          # a symlink to a file counts, as isfile did
        except OSError:
            return False

    @staticmethod
    def _file_mtime(path_or_entry):
        try:
            if isinstance(path_or_entry, os.DirEntry):
                return path_or_entry.stat().st_mtime
            return os.path.getmtime(path_or_entry)
        except OSError:
            return 0.0

    def _file_glob(self, base, pattern, state):
        """Files matching `pattern` under `base` as (path, mtime) pairs, with
        glob's semantics — `*` `?` `[]` per path component, `**` any depth, a
        wildcard never matching a leading dot — minus following symlinked
        directories. Unlike glob.glob it PRUNES as it descends (FILE_SKIP_DIRS,
        the remote roots) and ends at STOP / the ceiling, every directory
        listed once."""
        full = os.path.normpath(os.path.join(os.path.abspath(base), pattern))
        drive, tail = os.path.splitdrive(full)
        comps = [c for c in tail.split(os.sep) if c and c != "."]
        root = drive + os.sep
        # The literal lead-in (`src/lib` of `src/lib/**/*.py`) is the walk's
        # root. It is never pruned: naming a skip dir or a remote root — or
        # passing it as `path` — searches it.
        while comps and comps[0] != "**" and not glob.has_magic(comps[0]):
            root = os.path.join(root, comps.pop(0))
        if not comps:                           # a literal path, not a pattern
            return [(root, self._file_mtime(root))] if os.path.isfile(root) else []
        if not os.path.isdir(root):
            return []
        comps = [c for i, c in enumerate(comps)             # `**/**/x` is `**/x`
                 if not (c == "**" and i and comps[i - 1] == "**")]

        found = []
        stack = [(root, tuple(comps))]
        while stack:
            dirpath, comps = stack.pop()
            if not self._file_walk_continues(state, dirpath):
                break
            try:
                with os.scandir(dirpath) as it:
                    entries = list(it)
            except OSError:                     # unreadable (TCC, permissions, gone)
                continue
            if comps[0] == "**":
                # ** = zero or more non-hidden directories: match the rest
                # right here (an empty rest means every file below) AND carry
                # the ** into every enterable subdirectory.
                here, carry = (comps[1:] or ("*",)), comps
            else:
                here, carry = comps, None
            name_pat = here[0]
            for entry in entries:
                name = entry.name
                hidden = name.startswith(".")
                if (carry is not None and not hidden and self._file_entry_is_dir(entry)
                        and self._file_dir_enterable(dirpath, name, state)):
                    stack.append((entry.path, carry))
                if hidden and not name_pat.startswith("."):
                    continue
                if not fnmatch.fnmatch(name, name_pat):
                    continue
                if len(here) == 1:
                    if self._file_entry_is_file(entry):
                        found.append((entry.path, self._file_mtime(entry)))
                elif (self._file_entry_is_dir(entry)
                        and self._file_dir_enterable(dirpath, name, state)):
                    stack.append((entry.path, here[1:]))
        return found

    @staticmethod
    def _file_walk_notes(state):
        """What the model must know about a walk's result, appended to it."""
        notes = []
        if state["remote_hits"]:
            notes.append("Not entered (cloud-synced / network trees, or other apps' sandbox "
                         "containers — a stat inside one may wait on a server or raise a "
                         "permission dialog): " + ", ".join(state["remote_hits"])
                         + ". Pass one as 'path' to search it.")
        if state["stopped"]:
            notes.append("Walk ended by STOP — results are PARTIAL.")
        elif state["timed_out_at"]:
            elapsed = time.monotonic() - state["started"]
            notes.append(f"Walk stopped after {elapsed:.0f} s at {state['timed_out_at']} — "
                         "results are PARTIAL; narrow the pattern or pass a deeper 'path'.")
        return "".join(f"\n⚠ {n}" for n in notes)

    def _file_walk_announce(self, tool, state):
        """The same facts as activity lines in the output pane (a bare mixin
        host has no _tool_info; the tests run one)."""
        post = getattr(self, "_tool_info", None)
        if post is None:
            return
        for hit in state["remote_hits"]:
            post(f"{tool}: not entering {hit} (cloud-synced / network tree, or another app's container)\n")
        if state["stopped"]:
            post(f"{tool}: walk ended by STOP\n")
        elif state["timed_out_at"]:
            post(f"{tool}: walk stopped after {FILE_WALK_TIMEOUT:.0f} s — partial results\n")

    # ── tool implementations ────────────────────────────────────────────

    def do_read_file(self, inp):
        inp = inp or {}
        path = self._file_path(inp)
        if not path:
            return "read_file error: 'path' is required."
        if not os.path.isfile(path):
            return f"read_file error: file not found: {path}"
        lossy = False
        try:
            text = self._file_read_text(path)
        except UnicodeDecodeError:
            text, lossy = self._file_read_text(path, errors="replace"), True
        except OSError as e:
            return f"read_file error: {e}"
        if "\x00" in text[:8192]:
            return (f"read_file error: {path} looks binary. Use read_document for "
                    "PDF/DOCX or run_command for other binary formats.")
        self._file_reads().add(self._file_key(path))
        body, total, first, last = self._file_numbered(
            text, inp.get("offset") or 1, inp.get("limit") or FILE_DEFAULT_READ_LIMIT)
        if not body:
            return f"{path}: {total} lines total — offset {inp.get('offset')} is past the end."
        note = "" if last >= total else f"\n…[{total - last} more lines — re-read with offset={last + 1}]"
        if lossy:
            # Say so: it read as ordinary text, edit_file then refused it, and
            # a write_file of what was shown re-encoded every such byte.
            note += ("\n⚠ This file is not valid UTF-8 (a legacy code page?) — undecodable "
                     "bytes are shown as U+FFFD. edit_file refuses it, and write_file "
                     "would re-encode the whole file as UTF-8, replacing those bytes.")
        return f"{path} (lines {first}-{last} of {total}):\n{body}{note}"

    def do_write_file(self, inp):
        inp = inp or {}
        path = self._file_path(inp)
        content = inp.get("content", "")
        if not path:
            return "write_file error: 'path' is required."
        if not isinstance(content, str):
            # Refused BEFORE anything is touched (models send a JSON object
            # for a .json file; it used to empty the file and then fail).
            return (f"write_file error: 'content' must be a string (the file's text), "
                    f"not {type(content).__name__} — to write JSON, pass it serialized.")
        existed = os.path.isfile(path)
        if existed and self._file_key(path) not in self._file_reads():
            return (f"write_file refused: {path} already exists and has not been read "
                    "this session. read_file it first (overwrites must be informed), "
                    "or use edit_file for a partial change.")
        err = self._file_write_atomic(path, content)
        if err:
            return f"write_file error: {err}"
        self._file_reads().add(self._file_key(path))
        action = "Overwrote" if existed else "Created"
        return f"{action} {path} ({len(content)} chars, {len(content.splitlines())} lines)."

    def do_edit_file(self, inp):
        inp = inp or {}
        path = self._file_path(inp)
        if not path:
            return "edit_file error: 'path' is required."
        if not os.path.isfile(path):
            return f"edit_file error: file not found: {path}"
        if self._file_key(path) not in self._file_reads():
            return (f"edit_file refused: read_file {path} first — edits must be based "
                    "on the file's actual current content.")
        try:
            content = self._file_read_text(path)
        except UnicodeDecodeError:
            return f"edit_file error: {path} is not valid UTF-8 text."
        except OSError as e:
            return f"edit_file error: {e}"
        old, new = inp.get("old_string", ""), inp.get("new_string", "")
        if not isinstance(old, str) or not isinstance(new, str):
            return "edit_file error: old_string and new_string must be strings."
        new_content, count, err = self._file_apply_edit(
            content, old, new, bool(inp.get("replace_all", False)))
        if err:
            return f"edit_file error: {err}"
        err = self._file_write_atomic(path, new_content)
        if err:
            return f"edit_file error: {err}"
        plural = "s" if count != 1 else ""
        return f"Replaced {count} occurrence{plural} in {path}."

    def do_glob_files(self, inp):
        inp = inp or {}
        pattern = inp.get("pattern", "")
        base = self._file_path(inp) or os.getcwd()
        if not pattern:
            return "glob_files error: 'pattern' is required."
        if not os.path.isdir(base):
            return f"glob_files error: not a directory: {base}"
        state = self._file_walk_state()
        found = self._file_glob(base, pattern, state)
        self._file_walk_announce("glob_files", state)
        notes = self._file_walk_notes(state)
        mtimes = {}
        for path, mtime in found:               # one entry per file, however reached
            mtimes.setdefault(os.path.abspath(path), mtime)
        matches = sorted(mtimes, key=lambda p: (-mtimes[p], p))
        if not matches:
            return f"No files match {pattern!r} under {base}" + notes
        shown = matches[:200]
        more = "" if len(matches) <= 200 else f"\n…[{len(matches) - 200} more not shown — narrow the pattern]"
        return f"{len(matches)} file(s), newest first:\n" + "\n".join(shown) + more + notes

    def do_grep_files(self, inp):
        inp = inp or {}
        pattern = inp.get("pattern", "")
        base = self._file_path(inp) or os.getcwd()
        if not pattern:
            return "grep_files error: 'pattern' is required."
        mode = inp.get("output_mode") or "files_with_matches"
        if mode not in ("files_with_matches", "content", "count"):
            return f"grep_files error: unknown output_mode {mode!r}."
        try:
            rx = re.compile(pattern, re.IGNORECASE if inp.get("ignore_case") else 0)
        except re.error as e:
            return f"grep_files error: invalid regex: {e}"
        name_glob = inp.get("glob")
        max_results = int(inp.get("max_results") or (100 if mode == "content" else 50))

        state = None
        if os.path.isfile(base):
            candidates = [base]
        elif os.path.isdir(base):
            state = self._file_walk_state()
            candidates = self._file_grep_candidates(base, state)
        else:
            return f"grep_files error: path not found: {base}"

        out, hit_files, truncated = [], 0, False
        for fp in candidates:
            if name_glob and not self._file_glob_matches(fp, base, name_glob):
                continue
            lines = self._file_grep_lines(fp, rx)
            if lines is None or not lines:
                continue
            hit_files += 1
            if mode == "files_with_matches":
                out.append(os.path.abspath(fp))
            elif mode == "count":
                out.append(f"{os.path.abspath(fp)}: {len(lines)}")
            else:
                for lineno, ln in lines:
                    if len(ln) > 400:
                        ln = ln[:400] + " …[truncated]"
                    out.append(f"{os.path.abspath(fp)}:{lineno}: {ln}")
                    if len(out) >= max_results:
                        break
            if len(out) >= max_results:
                truncated = True
                break
        notes = ""
        if state is not None:                   # the walk is over: say what it skipped or cut short
            self._file_walk_announce("grep_files", state)
            notes = self._file_walk_notes(state)
        if not out:
            return f"No matches for {pattern!r} under {base}" + notes
        note = "\n…[results capped — narrow the search or raise max_results]" if truncated else ""
        unit = "line(s)" if mode == "content" else "file(s)"
        return f"{len(out)} matching {unit}:\n" + "\n".join(out) + note + notes

    # ── grep internals ──────────────────────────────────────────────────

    @staticmethod
    def _file_glob_matches(fp, base, pattern):
        """grep_files' `glob` filter: the file NAME ("*.py"), or — for a
        pattern with a slash — the path relative to the search root
        ("src/**/*.py"); a leading "**/" also matches at the root. It matched
        the basename only, so "**/*.py" silently found nothing."""
        name = os.path.basename(fp)
        if fnmatch.fnmatch(name, pattern):
            return True
        pattern = pattern.replace("\\", "/")
        if "/" not in pattern:
            return False
        try:
            rel = os.path.relpath(fp, base).replace(os.sep, "/")
        except ValueError:
            return False
        return (fnmatch.fnmatch(rel, pattern)
                or (pattern.startswith("**/") and fnmatch.fnmatch(rel, pattern[3:])))

    def _file_grep_candidates(self, base, state):
        """Every file under `base`, pruning as the walk descends (the rule
        shared with glob_files) and ending at STOP / the ceiling."""
        for root, dirs, files in os.walk(os.path.abspath(base)):
            if not self._file_walk_continues(state, root):
                return
            dirs[:] = [d for d in dirs if self._file_dir_enterable(root, d, state)]
            for fn in files:
                yield os.path.join(root, fn)

    @staticmethod
    def _file_grep_lines(fp, rx):
        """Matching (lineno, line) pairs, or None for skipped (binary/huge/unreadable)."""
        try:
            if os.path.getsize(fp) > FILE_GREP_MAX_FILE_BYTES:
                return None
            with open(fp, "rb") as f:
                raw = f.read()
        except OSError:
            return None
        if b"\x00" in raw[:8192]:
            return None
        text = raw.decode("utf-8", errors="replace")
        return [(i, ln) for i, ln in enumerate(text.splitlines(), start=1) if rx.search(ln)]
