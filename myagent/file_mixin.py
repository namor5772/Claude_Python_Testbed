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

All helpers are prefixed _file_* or are unique to this mixin (no MRO
shadowing risk); the pure cores (_file_apply_edit, _file_numbered) are
static for direct unit testing in tests/test_file_mixin.py.
"""
import glob
import os
import re


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


class FileMixin:
    """Native file tools with read-before-edit tracking."""

    # ── shared state ────────────────────────────────────────────────────

    @staticmethod
    def _file_key(path):
        return os.path.normcase(os.path.abspath(path))

    def _file_reads(self):
        # Lazy init so the mixin needs no App.__init__ wiring; process-lifetime
        # scope matches MyAgent's fire-and-forget one-run-per-process pattern.
        s = getattr(self, "_file_read_paths", None)
        if s is None:
            s = self._file_read_paths = set()
        return s

    # ── pure cores (unit-tested directly) ───────────────────────────────

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
        out = []
        for i, ln in enumerate(window, start=offset):
            if len(ln) > FILE_MAX_LINE_CHARS:
                ln = ln[:FILE_MAX_LINE_CHARS] + " …[line truncated]"
            out.append(f"{i:>6}\t{ln}")
        body = "\n".join(out)
        if len(body) > FILE_MAX_READ_CHARS:
            body = body[:FILE_MAX_READ_CHARS] + "\n…[output truncated — re-read with a smaller limit]"
        last = offset + len(window) - 1 if window else 0
        return body, total, (offset if window else 0), last

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
        count = content.count(o)
        if count == 0 and "\r\n" in content and "\r" not in o:
            o = old.replace("\n", "\r\n")
            n = new.replace("\n", "\r\n")
            count = content.count(o)
        if count == 0:
            return content, 0, ("old_string not found in the file. It must match the "
                                "current file content EXACTLY, including whitespace and "
                                "indentation. read_file the relevant section and retry.")
        if count > 1 and not replace_all:
            return content, 0, (f"old_string appears {count} times in the file — include "
                                "more surrounding lines to make it unique, or set "
                                "replace_all=true to replace every occurrence.")
        if replace_all:
            return content.replace(o, n), count, None
        return content.replace(o, n, 1), 1, None

    @staticmethod
    def _file_walk_skipped(rel_parts):
        return any(part in FILE_SKIP_DIRS for part in rel_parts)

    # ── tool implementations ────────────────────────────────────────────

    def do_read_file(self, inp):
        inp = inp or {}
        path = inp.get("path", "")
        if not path:
            return "read_file error: 'path' is required."
        if not os.path.isfile(path):
            return f"read_file error: file not found: {path}"
        try:
            text = self._file_read_text(path, errors="replace")
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
        return f"{path} (lines {first}-{last} of {total}):\n{body}{note}"

    def do_write_file(self, inp):
        inp = inp or {}
        path = inp.get("path", "")
        content = inp.get("content", "")
        if not path:
            return "write_file error: 'path' is required."
        existed = os.path.isfile(path)
        if existed and self._file_key(path) not in self._file_reads():
            return (f"write_file refused: {path} already exists and has not been read "
                    "this session. read_file it first (overwrites must be informed), "
                    "or use edit_file for a partial change.")
        try:
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        except OSError as e:
            return f"write_file error: {e}"
        self._file_reads().add(self._file_key(path))
        action = "Overwrote" if existed else "Created"
        return f"{action} {path} ({len(content)} chars, {len(content.splitlines())} lines)."

    def do_edit_file(self, inp):
        inp = inp or {}
        path = inp.get("path", "")
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
        new_content, count, err = self._file_apply_edit(
            content, inp.get("old_string", ""), inp.get("new_string", ""),
            bool(inp.get("replace_all", False)))
        if err:
            return f"edit_file error: {err}"
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new_content)
        except OSError as e:
            return f"edit_file error: {e}"
        plural = "s" if count != 1 else ""
        return f"Replaced {count} occurrence{plural} in {path}."

    def do_glob_files(self, inp):
        inp = inp or {}
        pattern = inp.get("pattern", "")
        base = inp.get("path") or os.getcwd()
        if not pattern:
            return "glob_files error: 'pattern' is required."
        if not os.path.isdir(base):
            return f"glob_files error: not a directory: {base}"
        matches = []
        for p in glob.glob(os.path.join(base, pattern), recursive=True):
            if not os.path.isfile(p):
                continue
            rel = os.path.normpath(os.path.relpath(p, base)).split(os.sep)
            if self._file_walk_skipped(rel[:-1]):
                continue
            matches.append(os.path.abspath(p))

        def _mtime(p):
            try:
                return os.path.getmtime(p)
            except OSError:
                return 0.0

        matches.sort(key=_mtime, reverse=True)
        if not matches:
            return f"No files match {pattern!r} under {base}"
        shown = matches[:200]
        note = "" if len(matches) <= 200 else f"\n…[{len(matches) - 200} more not shown — narrow the pattern]"
        return f"{len(matches)} file(s), newest first:\n" + "\n".join(shown) + note

    def do_grep_files(self, inp):
        inp = inp or {}
        pattern = inp.get("pattern", "")
        base = inp.get("path") or os.getcwd()
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

        if os.path.isfile(base):
            candidates = [base]
        elif os.path.isdir(base):
            candidates = self._file_grep_candidates(base, name_glob)
        else:
            return f"grep_files error: path not found: {base}"

        import fnmatch  # stdlib; local import keeps module top minimal
        out, hit_files, truncated = [], 0, False
        for fp in candidates:
            if name_glob and not fnmatch.fnmatch(os.path.basename(fp), name_glob):
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
        if not out:
            return f"No matches for {pattern!r} under {base}"
        note = "\n…[results capped — narrow the search or raise max_results]" if truncated else ""
        unit = "line(s)" if mode == "content" else "file(s)"
        return f"{len(out)} matching {unit}:\n" + "\n".join(out) + note

    # ── grep internals ──────────────────────────────────────────────────

    @staticmethod
    def _file_grep_candidates(base, name_glob):
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in FILE_SKIP_DIRS]
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
