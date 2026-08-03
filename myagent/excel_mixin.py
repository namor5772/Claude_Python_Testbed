"""Excel live-workbook automation tools (xlwings).

Drives the REAL Excel application — COM on Windows, AppleScript on macOS —
so changes land visibly in the open workbook, formulas recalculate, and
existing VBA macros can run. This is the "agent inside your workbook"
surface: attach to whatever the user already has open (or open/create a
workbook), then read/write/format ranges, manage sheets, run macros.

Architecture notes (mirrors the mail mixins):
- No COM/AppleScript handles are cached on self. Tool calls run on a fresh
  stream_worker thread each run, and COM objects must not cross threads —
  so every call re-attaches to the running Excel instance (cheap, via the
  Running Object Table on Windows). Workbook state lives in the Excel
  process itself, not in MyAgent.
- All helpers are prefixed _excel_ against flat-namespace MRO shadowing
  (the Proton/Outlook lesson in CLAUDE_MYAGENT.md).
- xlwings import failure leaves the module importable (_HAS_EXCEL in
  constants gates the checkbox and dispatch); Excel-not-installed is
  detected at first-call time, like Proton Bridge availability.
"""

import datetime as _dt
import decimal as _decimal
import numbers as _numbers
import os
import sys

from myagent.constants import EXCEL_SHEET_ACTIONS

try:
    import xlwings as xw
except Exception:
    xw = None


class ExcelMixin:

    #: excel_sheet actions, in the order the tool description lists them.
    #: Defined in constants.py beside the tool schema (whose enum it also
    #: feeds), so the guard and the schema can never drift apart.
    SHEET_ACTIONS = EXCEL_SHEET_ACTIONS

    # ── Pure helpers (unit-tested in tests/test_excel_mixin.py) ──────────

    @staticmethod
    def _excel_col_letter(n):
        """1-based column number → Excel letters (1→A, 27→AA)."""
        letters = ""
        while n > 0:
            n, rem = divmod(n - 1, 26)
            letters = chr(65 + rem) + letters
        return letters

    @staticmethod
    def _excel_hex_to_rgb(color):
        """'#RRGGBB' (hash optional) → (r, g, b) tuple for xlwings."""
        c = str(color).strip().lstrip("#")
        if len(c) != 6:
            raise ValueError(f"color must be hex RRGGBB, got '{color}'")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))

    @staticmethod
    def _excel_cell_str(v):
        """One cell value → display string for the model. Integral floats
        drop the '.0' (COM returns all numbers as float); midnight
        datetimes render as bare dates; tabs/newlines are flattened so
        TSV rows stay rectangular."""
        if v is None:
            return ""
        if v is True:
            return "TRUE"
        if v is False:
            return "FALSE"
        if isinstance(v, _decimal.Decimal):
            # COM returns currency-formatted cells as VT_CY → Decimal
            # (with trailing zeros like 7.5000); normalize to float display.
            v = float(v)
        if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        if isinstance(v, _dt.datetime):
            if v.hour == v.minute == v.second == 0 and v.microsecond == 0:
                return v.strftime("%Y-%m-%d")
            return v.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, _dt.date):
            return v.isoformat()
        return str(v).replace("\t", " ").replace("\r", " ").replace("\n", " ")

    @staticmethod
    def _excel_as_2d(v):
        """Normalize a range .value/.formula result to a 2D list. COM and
        AppleScript return a scalar for one cell and (on some paths) a flat
        sequence for a single row."""
        if not isinstance(v, (list, tuple)):
            return [[v]]
        rows = list(v)
        if not rows:
            return [[None]]
        if not isinstance(rows[0], (list, tuple)):
            return [rows]
        return [list(r) for r in rows]

    @staticmethod
    def _excel_values_matrix(values):
        """Normalize the excel_write 'values' input to a rectangular 2D
        list. Scalar → [[v]]; flat list → one row; ragged rows are padded
        with None (empty cell). '' and None both mean empty."""
        if not isinstance(values, list):
            rows = [[values]]
        elif not values:
            raise ValueError("'values' is empty")
        elif not any(isinstance(r, list) for r in values):
            rows = [values]
        else:
            rows = [r if isinstance(r, list) else [r] for r in values]
        width = max(len(r) for r in rows)
        if width == 0:
            raise ValueError("'values' contains only empty rows")
        out = []
        for r in rows:
            row = [None if c is None or c == "" else c for c in r]
            row += [None] * (width - len(row))
            out.append(row)
        return out

    @staticmethod
    def _excel_open_kwargs(params):
        """Assemble books.open() kwargs from the tool params, omitting
        anything not supplied so an ordinary open stays byte-identical to a
        bare open(path).

        The two password fields are DIFFERENT locks and a workbook can carry
        both: `password` decrypts the file, `write_res_password` claims write
        access on a write-reserved one. Missing the second is what leaves an
        unattended run parked on a modal nothing can answer."""
        kwargs = {}
        for key in ("password", "write_res_password"):
            val = (params.get(key) or "").strip()
            if val:
                kwargs[key] = val
        if params.get("ignore_read_only_recommended"):
            kwargs["ignore_read_only_recommended"] = True
        return kwargs

    @staticmethod
    def _excel_write_dropped(expected, actual):
        """True when a write that should have produced visible cells read
        back completely empty — the signature of an Excel instance that is
        silently discarding Apple Event writes (found live on macOS
        2026-08-01: reads, formatting and sheet ops keep working while every
        value write is dropped, and .books.count still reports healthy).

        Deliberately conservative: it fires only when EVERY expected cell was
        non-empty and EVERY cell read back empty, so a legitimately blank
        result (a formula returning "") can't trip it."""
        exp = [c for row in expected for c in row]
        act = [c for row in actual for c in row]
        if not exp or not act:
            return False
        if any(c is None or c == "" for c in exp):
            return False
        return all(c is None or c == "" for c in act)

    @staticmethod
    def _excel_matrix_tsv(matrix, first_row, first_col):
        """Render a 2D matrix as TSV with REAL column letters and row
        numbers so the model can address any cell it sees directly."""
        n_cols = len(matrix[0]) if matrix else 0
        header = "\t" + "\t".join(
            ExcelMixin._excel_col_letter(first_col + c) for c in range(n_cols))
        lines = [header]
        for i, row in enumerate(matrix):
            cells = "\t".join(ExcelMixin._excel_cell_str(v) for v in row)
            lines.append(f"{first_row + i}\t{cells}")
        return "\n".join(lines)

    # ── Excel plumbing ───────────────────────────────────────────────────

    @staticmethod
    def _excel_com_init():
        """Per-call COM apartment init (Windows only; idempotent per
        thread). Required because each agent run executes tools on a new
        stream_worker thread. No-op on macOS (AppleScript backend)."""
        if sys.platform == "win32":
            try:
                import pythoncom
                pythoncom.CoInitialize()
            except Exception:
                pass

    @staticmethod
    def _excel_err(e):
        """COM errors bury the useful text three levels deep; dig it out."""
        if type(e).__name__ == "com_error":
            try:
                details = e.args[2]
                if details and details[2]:
                    return str(details[2])
            except Exception:
                pass
        return str(e)

    def _excel_app(self, launch=False):
        """Attach to the running Excel instance (the user's, if one is
        open) or launch a visible one when launch=True.

        The chokepoint every tool passes through (directly or via
        _excel_book/_excel_target), so the xlwings-missing guard and the
        per-thread COM init live here instead of being repeated in each
        do_excel_* method."""
        if xw is None:
            raise RuntimeError(
                "xlwings is not installed (pip install xlwings).")
        self._excel_com_init()
        app = None
        try:
            if xw.apps.count:
                app = xw.apps.active
                # Liveness probe: a just-quit instance can linger in the COM
                # Running Object Table with a dead window handle, surfacing
                # later as a cryptic FindWindowEx error. Touching .books here
                # turns that into "no instance" (and a fresh launch if asked).
                _ = app.books.count
        except Exception:
            app = None
        if app is None:
            if not launch:
                raise RuntimeError(
                    "No running Excel instance. Call excel_open first — it "
                    "attaches to the user's open Excel or launches it.")
            app = xw.App(visible=True, add_book=False)
        try:
            app.visible = True  # the point is watching the workbook change
        except Exception:
            pass
        return app

    @staticmethod
    def _excel_api_bool(book, win_name, mac_name):
        """Read a boolean workbook property across both backends, or None if
        neither shape answers. COM exposes a plain attribute (`ReadOnly`);
        appscript exposes a reference needing .get() (`read_only`). Probing
        the wrong one raises, so the chain must fall through rather than
        give up — and an unanswerable probe returns None so callers never
        invent a state they could not actually observe."""
        for probe in (lambda: getattr(book.api, win_name),          # Windows
                      lambda: getattr(book.api, mac_name).get()):   # macOS
            try:
                value = probe()
            except Exception:
                continue
            if value is not None:
                return bool(value)
        return None

    @staticmethod
    def _excel_is_read_only(book):
        """True when the workbook opened read-only. Read-only is SILENT and
        only bites at the first save, so this is what turns it into an
        up-front warning instead of a lost run."""
        return bool(ExcelMixin._excel_api_bool(book, "ReadOnly", "read_only"))

    @staticmethod
    def _excel_lock_file(path):
        """Office's owner-lock sidecar for `path` (`~$Name.xlsm`), or None if
        absent. A crashed or force-killed Excel leaves this behind, and a
        stale one makes every later open silently READ-ONLY."""
        lock = os.path.join(os.path.dirname(path),
                            "~$" + os.path.basename(path))
        return lock if os.path.exists(lock) else None

    def _excel_read_only_note(self, book, path):
        """Report a read-only open loudly, and list what to check.

        Read-only is silent and only bites at the first save, so the warning
        itself is the point. The CAUSE is deliberately not asserted: live
        2026-08-01 DeathBook.xlsm opened read-only for one agent run and
        fully editable minutes later — same password, no write reservation,
        file untouched — and neither a sidecar lock file nor Excel session
        state reproduced it on demand. So state the facts that ARE checkable
        and give the remedy that actually worked (retry on a fresh Excel)
        rather than inventing a diagnosis."""
        checks = []
        if self._excel_lock_file(path):
            checks.append(
                "an Office owner-lock file (~$…) sits beside it, so Excel may "
                "consider it open elsewhere — if it is genuinely not open on "
                "another machine, that lock is stale")
        if self._excel_api_bool(book, "WriteReserved", "write_reserved"):
            checks.append(
                "the workbook IS write-reserved — supply "
                "'write_res_password' (a second password, separate from "
                "'password')")
        if not os.access(path, os.W_OK):
            checks.append("the file is not writable on disk")
        checks.append(
            "it may be open on another machine or still checked out by "
            "OneDrive — quitting Excel completely and retrying has resolved "
            "this in practice")
        return ("⚠ Opened READ-ONLY — edits apply in memory but every save "
                "will FAIL. Check: " + "; ".join(checks) + ". ")

    def _excel_book(self, workbook=None):
        """Resolve a workbook by name (case-insensitive, extension
        optional) or return the active one."""
        app = self._excel_app(launch=False)
        books = list(app.books)
        if not books:
            raise RuntimeError(
                "Excel is running but no workbook is open. Use excel_open "
                "with a path (create=true to make a new one).")
        if not workbook:
            book = app.books.active
            if book is None:
                book = books[0]
            return book
        want = os.path.basename(str(workbook).strip()).lower()
        want_stem = os.path.splitext(want)[0]
        for b in books:
            name = b.name.lower()
            if name == want or os.path.splitext(name)[0] == want_stem:
                return b
        names = ", ".join(b.name for b in books)
        raise RuntimeError(
            f"Workbook '{workbook}' is not open. Open workbooks: {names}. "
            "Use excel_open to open a file from disk.")

    @staticmethod
    def _excel_resolve_sheet_name(names, want):
        """Pick the real sheet name matching `want`, or None. Exact first,
        then case-insensitive (Windows COM's lookup is case-insensitive and
        macOS's is not — this keeps both platforms behaving the same)."""
        want = str(want).strip()
        if want in names:
            return want
        folded = want.lower()
        for n in names:
            if n.lower() == folded:
                return n
        return None

    @staticmethod
    def _excel_get_sheet(book, sheet=None):
        if not sheet:
            return book.sheets.active
        # Validate against the real names rather than trusting the lookup to
        # raise: on macOS book.sheets['NoSuch'] returns a LAZY reference that
        # only fails later at use (leaking a raw OSERROR -1728 from whatever
        # call happened to touch it), so the friendly error never fired.
        names = [s.name for s in book.sheets]
        real = ExcelMixin._excel_resolve_sheet_name(names, sheet)
        if real is None:
            raise RuntimeError(
                f"Sheet '{sheet}' not found in {book.name}. "
                f"Sheets: {', '.join(names)}")
        return book.sheets[real]

    def _excel_target(self, params):
        book = self._excel_book(params.get("workbook"))
        sht = self._excel_get_sheet(book, params.get("sheet"))
        return book, sht

    def _excel_state_summary(self, app):
        books = list(app.books)
        if not books:
            return "Excel is running with no workbooks open."
        active_book = app.books.active
        active_name = active_book.name if active_book else ""
        lines = ["Open workbooks:"]
        for b in books:
            marker = " (active)" if b.name == active_name else ""
            sheets = [s.name for s in b.sheets]
            shown = ", ".join(sheets[:15])
            if len(sheets) > 15:
                shown += f", … +{len(sheets) - 15} more"
            lines.append(f"• {b.name}{marker} — sheets: {shown}")
        if active_book:
            sht = active_book.sheets.active
            ur = sht.used_range
            addr = ur.address.replace("$", "")
            lines.append(
                f"Active sheet: [{active_book.name}]{sht.name}, used range "
                f"{addr} ({ur.rows.count} rows x {ur.columns.count} cols)")
        return "\n".join(lines)

    # ── Tools ────────────────────────────────────────────────────────────

    def do_excel_open(self, params):
        try:
            path = (params.get("path") or "").strip()
            app = self._excel_app(launch=True)
            note = ""
            if path:
                ap = os.path.abspath(os.path.expanduser(path))
                base = os.path.basename(ap).lower()
                book = next(
                    (b for b in app.books if b.name.lower() == base), None)
                if book is not None:
                    note = f"Attached to already-open {book.name}. "
                elif os.path.exists(ap):
                    # These only apply to opening from disk; a wrong password
                    # errors immediately (no dialog). Kwargs are passed only
                    # when given, so an unprotected open stays byte-identical.
                    book = app.books.open(ap, **self._excel_open_kwargs(params))
                    note = f"Opened {ap}. "
                    if self._excel_is_read_only(book):
                        note += self._excel_read_only_note(book, ap)
                elif params.get("create"):
                    book = app.books.add()
                    book.save(ap)
                    note = f"Created new workbook {ap}. "
                else:
                    return (f"excel_open error: file not found: {ap}. Pass "
                            "create=true to create a new workbook there.")
                book.activate()
            return note + self._excel_state_summary(app)
        except Exception as e:
            return f"excel_open error: {self._excel_err(e)}"

    def do_excel_read(self, params):
        try:
            book, sht = self._excel_target(params)
            rng_str = (params.get("range") or "").strip()
            rng = sht.range(rng_str) if rng_str else sht.used_range
            rows, cols = rng.rows.count, rng.columns.count
            try:
                max_cells = int(params.get("max_cells") or 4000)
            except (TypeError, ValueError):
                max_cells = 4000
            max_cells = max(1, min(max_cells, 100_000))
            shown_cols = min(cols, max_cells)
            shown_rows = min(rows, max(1, max_cells // shown_cols))
            sub = rng
            if shown_rows < rows or shown_cols < cols:
                sub = rng.resize(shown_rows, shown_cols)
            if params.get("formulas"):
                kind = "formulas"
                matrix = self._excel_as_2d(sub.formula)
            else:
                kind = "values"
                matrix = sub.options(ndim=2).value
            addr = rng.address.replace("$", "")
            out = (f"[{book.name}]{sht.name}!{addr} — {rows}x{cols} {kind}\n"
                   + self._excel_matrix_tsv(matrix, rng.row, rng.column))
            if shown_rows < rows or shown_cols < cols:
                out += (f"\n… truncated to {shown_rows}x{shown_cols} "
                        f"(max_cells={max_cells}). Read a smaller range or "
                        "raise max_cells for the rest.")
            return out
        except Exception as e:
            return f"excel_read error: {self._excel_err(e)}"

    def do_excel_write(self, params):
        try:
            book, sht = self._excel_target(params)
            start = (params.get("start_cell") or "").strip()
            if not start:
                return "excel_write error: 'start_cell' is required (e.g. 'B2')."
            matrix = self._excel_values_matrix(params.get("values"))
            target = sht.range(start).resize(len(matrix), len(matrix[0]))
            target.value = matrix
            addr = target.address.replace("$", "")
            out = (f"Wrote {len(matrix)}x{len(matrix[0])} cells to "
                   f"[{book.name}]{sht.name}!{addr}.")
            if len(matrix) * len(matrix[0]) <= 200:
                back = target.options(ndim=2).value
                probe_expected, probe_actual = matrix, back
                out += ("\nCurrent values (after recalculation):\n"
                        + self._excel_matrix_tsv(back, target.row, target.column))
            else:
                # Large writes get no echo (it would swamp the model), which
                # left them with NO verification at all — a silently-dropping
                # Excel would still report "Wrote NxN cells". Probe the first
                # row instead: one cheap read that catches the same failure.
                probe_cols = min(len(matrix[0]), 20)
                probe = target.resize(1, probe_cols)
                probe_expected = [matrix[0][:probe_cols]]
                probe_actual = probe.options(ndim=2).value
            if self._excel_write_dropped(probe_expected, probe_actual):
                out += ("\n⚠ VERIFICATION FAILED: the cells read back EMPTY "
                        "after writing. This Excel instance is discarding "
                        "writes (reads and formatting still work, so it looks "
                        "healthy). Nothing was written. Quit Excel completely, "
                        "reopen it, and retry — do not assume the data landed.")
            return out
        except Exception as e:
            return f"excel_write error: {self._excel_err(e)}"

    def do_excel_format(self, params):
        try:
            book, sht = self._excel_target(params)
            rng_str = (params.get("range") or "").strip()
            if not rng_str:
                return "excel_format error: 'range' is required (e.g. 'A1:D1')."
            rng = sht.range(rng_str)
            applied = []
            if params.get("bold") is not None:
                rng.font.bold = bool(params["bold"])
                applied.append(f"bold={bool(params['bold'])}")
            if params.get("italic") is not None:
                rng.font.italic = bool(params["italic"])
                applied.append(f"italic={bool(params['italic'])}")
            if params.get("font_size") is not None:
                rng.font.size = float(params["font_size"])
                applied.append(f"font_size={params['font_size']}")
            if params.get("font_color"):
                rng.font.color = self._excel_hex_to_rgb(params["font_color"])
                applied.append(f"font_color={params['font_color']}")
            if params.get("fill_color"):
                if str(params["fill_color"]).strip().lower() == "none":
                    rng.color = None
                    applied.append("fill_color=none")
                else:
                    rng.color = self._excel_hex_to_rgb(params["fill_color"])
                    applied.append(f"fill_color={params['fill_color']}")
            if params.get("number_format"):
                rng.number_format = str(params["number_format"])
                applied.append(f"number_format={params['number_format']}")
            if params.get("column_width") is not None:
                rng.column_width = float(params["column_width"])
                applied.append(f"column_width={params['column_width']}")
            if params.get("autofit"):
                rng.autofit()
                applied.append("autofit")
            if not applied:
                return ("excel_format error: no formatting properties given. "
                        "Provide at least one of bold, italic, font_size, "
                        "font_color, fill_color, number_format, column_width, "
                        "autofit.")
            addr = rng.address.replace("$", "")
            return (f"Applied {', '.join(applied)} to "
                    f"[{book.name}]{sht.name}!{addr}.")
        except Exception as e:
            return f"excel_format error: {self._excel_err(e)}"

    def do_excel_sheet(self, params):
        try:
            action = (params.get("action") or "").strip().lower()
            # Validate the action BEFORE resolving the sheet, so a bad action
            # is named as such instead of surfacing as "sheet not found" for
            # whatever placeholder name came along with it.
            if action not in self.SHEET_ACTIONS:
                return (f"excel_sheet error: unknown action '{action}'. Use "
                        + ", ".join(self.SHEET_ACTIONS) + ".")
            book = self._excel_book(params.get("workbook"))
            name = (params.get("name") or "").strip()
            if action == "list":
                lines = []
                active = book.sheets.active.name
                for s in book.sheets:
                    ur = s.used_range
                    marker = " (active)" if s.name == active else ""
                    lines.append(
                        f"• {s.name}{marker} — used range "
                        f"{ur.address.replace('$', '')} "
                        f"({ur.rows.count}x{ur.columns.count})")
                return f"Sheets in {book.name}:\n" + "\n".join(lines)
            if not name:
                return f"excel_sheet error: 'name' is required for '{action}'."
            if action == "add":
                book.sheets.add(name, after=book.sheets[book.sheets.count - 1])
                return (f"Added sheet '{name}' to {book.name}. Sheets: "
                        + ", ".join(s.name for s in book.sheets))
            sht = self._excel_get_sheet(book, name)
            if action == "rename":
                new_name = (params.get("new_name") or "").strip()
                if not new_name:
                    return "excel_sheet error: 'new_name' is required for rename."
                sht.name = new_name
                return f"Renamed sheet '{name}' to '{new_name}' in {book.name}."
            if action == "delete":
                sht.delete()
                return (f"Deleted sheet '{name}' from {book.name}. Sheets: "
                        + ", ".join(s.name for s in book.sheets))
            if action == "activate":
                book.activate()
                sht.activate()
                return f"Activated sheet '{name}' in {book.name}."
            if action == "clear":
                sht.clear()
                return f"Cleared all contents and formats of sheet '{name}'."
            # Unreachable: the guard above admits only actions handled here.
            # Kept so adding a name to SHEET_ACTIONS without a branch fails
            # loudly instead of returning None.
            return (f"excel_sheet error: action '{action}' is accepted but "
                    "not implemented — this is a bug in excel_mixin.")
        except Exception as e:
            return f"excel_sheet error: {self._excel_err(e)}"

    def do_excel_find(self, params):
        try:
            text = str(params.get("text") or "").strip()
            if not text:
                return "excel_find error: 'text' is required."
            book = self._excel_book(params.get("workbook"))
            if params.get("sheet"):
                sheets = [self._excel_get_sheet(book, params["sheet"])]
            else:
                sheets = list(book.sheets)
            try:
                max_results = int(params.get("max_results") or 50)
            except (TypeError, ValueError):
                max_results = 50
            needle = text.lower()
            try:
                num = float(text)
            except ValueError:
                num = None
            hits, truncated = [], False
            for sht in sheets:
                ur = sht.used_range
                if ur.rows.count * ur.columns.count > 200_000:
                    hits.append(f"[skipped sheet '{sht.name}': "
                                f"{ur.rows.count}x{ur.columns.count} cells is "
                                "too large to scan — search it with excel_read "
                                "on smaller ranges]")
                    continue
                matrix = ur.options(ndim=2).value
                r0, c0 = ur.row, ur.column
                for i, row in enumerate(matrix):
                    for j, v in enumerate(row):
                        # numbers.Number (not int/float) because COM returns
                        # currency-formatted cells as decimal.Decimal.
                        match = (isinstance(v, str) and needle in v.lower()) or \
                                (num is not None
                                 and isinstance(v, _numbers.Number)
                                 and not isinstance(v, bool)
                                 and float(v) == num)
                        if match:
                            addr = (f"{sht.name}!"
                                    f"{self._excel_col_letter(c0 + j)}{r0 + i}")
                            hits.append(f"{addr}: {self._excel_cell_str(v)}")
                            if len(hits) >= max_results:
                                truncated = True
                                break
                    if truncated:
                        break
                if truncated:
                    break
            if not hits:
                return (f"No cells matching '{text}' in {book.name} "
                        f"({', '.join(s.name for s in sheets)}).")
            out = f"Matches for '{text}' in {book.name}:\n" + "\n".join(hits)
            if truncated:
                out += f"\n… stopped at max_results={max_results}."
            return out
        except Exception as e:
            return f"excel_find error: {self._excel_err(e)}"

    def do_excel_run_macro(self, params):
        try:
            macro = (params.get("macro") or "").strip()
            if not macro:
                return "excel_run_macro error: 'macro' is required."
            book = self._excel_book(params.get("workbook"))
            args = params.get("args") or []
            result = book.macro(macro)(*args)
            if sys.platform == "win32":
                # COM raises for an unknown macro, so reaching here means it ran.
                return (f"Macro '{macro}' ran in {book.name}. "
                        f"Return value: {result!r}")
            # macOS cannot confirm this. AppleScript's `run VB macro` returns
            # None for a MISSING macro exactly as it does for one that ran and
            # returned nothing, and Excel's dictionary exposes no way to
            # enumerate VBA project members — so claiming success here would
            # report a typo'd or hallucinated macro name as a clean run.
            return (f"Macro '{macro}' was dispatched to {book.name}. "
                    f"Return value: {result!r}. NOTE (macOS): Excel does not "
                    "report whether the macro exists — a missing macro also "
                    "returns None. Confirm the macro's effect (e.g. with "
                    "excel_read) before relying on it having run.")
        except Exception as e:
            return (f"excel_run_macro error: {self._excel_err(e)} "
                    "(check the macro name exists in that workbook and that "
                    "macros are enabled)")

    def do_excel_save(self, params):
        try:
            book = self._excel_book(params.get("workbook"))
            path = (params.get("path") or "").strip()
            if path:
                book.save(os.path.abspath(os.path.expanduser(path)))
            elif os.path.dirname(book.fullname):
                book.save()
            else:
                return (f"excel_save error: '{book.name}' has never been "
                        "saved — pass 'path' to choose where to save it.")
            return f"Saved {book.fullname}"
        except Exception as e:
            return f"excel_save error: {self._excel_err(e)}"

    def do_excel_close(self, params):
        try:
            save = params.get("save", True)
            quit_app = bool(params.get("quit_app"))
            app = self._excel_app(launch=False)
            closed = ""
            if app.books.count:
                book = self._excel_book(params.get("workbook"))
                name = book.name
                note = ""
                if save and self._excel_is_read_only(book):
                    # Saving a read-only workbook fails deep in the driver
                    # (a bare OSERROR -50 / COM error naming .save), which
                    # reads as "close broke" rather than "your edits are
                    # gone". Say what actually happened.
                    note = (" — ⚠ NOT SAVED: the workbook was open READ-ONLY, "
                            "so any edits made in this session are LOST")
                elif save and os.path.dirname(book.fullname):
                    book.save()
                    note = " (saved)"
                elif save:
                    note = " (never saved to disk — contents discarded)"
                book.close()
                closed = f"Closed {name}{note}."
            if not quit_app:
                return closed or "No workbook open to close."
            # quit_app only quits when NOTHING else remains open — the agent
            # may be attached to the user's own Excel instance, and quitting
            # over their other workbooks would close them out from under them
            # (found live 2026-08-01: a smoke test's quit saved-and-closed the
            # user's open personal workbook).
            remaining = [b.name for b in app.books]
            if remaining:
                return ((closed + " " if closed else "")
                        + "Excel left running — other workbooks are still "
                        f"open: {', '.join(remaining)}. Close them explicitly "
                        "by name if that is really intended.")
            app.quit()
            return (closed + " " if closed else "") + "Excel closed."
        except Exception as e:
            return f"excel_close error: {self._excel_err(e)}"
