"""Excel live-workbook automation tools (xlwings).

Drives the REAL Excel application — COM on Windows, AppleScript on macOS —
so changes land visibly in the open workbook, formulas recalculate, and
existing VBA macros can run. This is the "agent inside your workbook"
surface: attach to whatever the user already has open (or open/create a
workbook), then read/write/format ranges, manage sheets and tables, freeze
panes, run macros.

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
- On macOS the import also moves xlwings' per-user config path OUT of
  Excel's sandbox container (see under the import): one stat in there is a
  TCC "access data from other apps" dialog on every new process.
- Three things (2026-11-18) reach past xlwings to the native object model,
  each in ONE place, because xlwings has no desktop implementation of them:
  excel_table's convert_to_range (_excel_table_unlist); a window's view —
  excel_freeze_panes and excel_zoom (_excel_pane_window hands out the
  window; _excel_win_get / _excel_win_set are where COM and appscript
  differ); and moving an existing sheet (_excel_sheet_native_move). The
  view goes through the WORKBOOK'S OWN window, never the application's
  active one — attached to the user's Excel, the active window is whatever
  they have in front. A move is made in the ONE form that cannot send a
  sheet out of its workbook. What a real Excel does with each, and why
  every step and every refusal is there, was measured that day:
  docs/excel-table-freeze-panes-2026-11-18.md.
"""

import datetime as _dt
import decimal as _decimal
import math as _math
import numbers as _numbers
import os
import re as _re
import sys

from myagent.constants import (
    EXCEL_SHEET_ACTIONS, EXCEL_TABLE_ACTIONS, EXCEL_TABLE_OPTIONS,
)

# What Excel reads as a cell reference, and so refuses as a table name: A1
# style (up to three column letters and a row) and R1C1 style (R1C1, RC, R2C,
# the bare R and C). Whether the row / column is inside the sheet is checked
# by the caller — XFE1 and A1048577 are past its edge and are valid names.
_A1_REF = _re.compile(r"^([A-Za-z]{1,3})(\d{1,7})$")
_R1C1_REF = _re.compile(r"^(?:[Rr](\d*))?(?:[Cc](\d*))?$")

try:
    import xlwings as xw
except Exception:
    xw = None
else:
    if sys.platform == "darwin":
        # xlwings keeps its per-user config INSIDE Excel's sandbox container
        # (~/Library/Containers/com.microsoft.Excel/Data/xlwings.conf) so that
        # Excel's own RunPython add-in, confined to that container, can read
        # it. MyAgent drives Excel from the outside and never uses RunPython —
        # yet xlwings stats that path from Python whenever an open workbook's
        # name is a OneDrive / SharePoint URL (Book.fullname →
        # fullname_url_to_local_path → read_user_config), and since macOS 15
        # one stat inside another app's container raises the "would like to
        # access data from other apps" dialog, a per-process consent macOS
        # never remembers (seen 2026-10-06). The config is read from the
        # per-user path xlwings uses everywhere else instead; nothing of
        # MyAgent's lives at either path.
        xw.USER_CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".xlwings", "xlwings.conf")


class ExcelMixin:

    #: excel_sheet actions, in the order the tool description lists them.
    #: Defined in constants.py beside the tool schema (whose enum it also
    #: feeds), so the guard and the schema can never drift apart.
    SHEET_ACTIONS = EXCEL_SHEET_ACTIONS

    #: excel_table's actions and display options — constants.py's too, for
    #: the same reason (the options also feed the schema's boolean properties).
    TABLE_ACTIONS = EXCEL_TABLE_ACTIONS
    TABLE_OPTIONS = EXCEL_TABLE_OPTIONS

    #: A worksheet's size, and the style Excel itself gives a new table.
    _EXCEL_SHEET_ROWS = 1_048_576
    _EXCEL_SHEET_COLS = 16_384
    _EXCEL_TABLE_STYLE = "TableStyleMedium2"
    _EXCEL_TABLE_STYLE_HINT = ("Built-in styles are TableStyleLight1-21, "
                               "TableStyleMedium1-28 and TableStyleDark1-11.")
    #: How many of a table's columns excel_table names before summing up the rest.
    _EXCEL_TABLE_COLS_SHOWN = 40
    #: What Excel takes for a sheet name and for a zoom (measured 2026-11-18).
    _EXCEL_SHEET_NAME_MAX = 31
    _EXCEL_SHEET_NAME_BAD = "\\/?*[]:"
    _EXCEL_ZOOM_MIN, _EXCEL_ZOOM_MAX = 10, 400

    #: A window's view properties — panes and zoom: the name this module uses
    #: → (the COM attribute, the appscript property). Reads and writes go
    #: through _excel_win_get / _excel_win_set, the one place the two shapes
    #: differ.
    _EXCEL_PANE_PROPS = {
        "frozen": ("FreezePanes", "freeze_panes"),
        "split_row": ("SplitRow", "split_row"),
        "split_column": ("SplitColumn", "split_column"),
        "scroll_row": ("ScrollRow", "scroll_row"),
        "scroll_column": ("ScrollColumn", "scroll_column"),
        "view": ("View", "view"),
        "zoom": ("Zoom", "zoom"),
    }

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
        non-empty and EVERY cell read back empty — and never for a write that
        holds a formula: its read-back is the formula's RESULT, which may be
        legitimately blank (=IF(A1="","",A1), IFERROR(…,"")). Counting the
        formula text as "expected non-empty" raised a false VERIFICATION
        FAILED over a write that had landed."""
        exp = [c for row in expected for c in row]
        act = [c for row in actual for c in row]
        if not exp or not act:
            return False
        if any(c is None or c == "" for c in exp):
            return False
        if any(isinstance(c, str) and c.startswith("=") for c in exp):
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

    @staticmethod
    def _excel_col_number(letters):
        """Excel column letters → 1-based number (A→1, XFD→16384): the
        inverse of _excel_col_letter."""
        n = 0
        for ch in str(letters).upper():
            n = n * 26 + (ord(ch) - 64)
        return n

    @staticmethod
    def _excel_is_cell_reference(name):
        """True when `name` reads as a cell reference in either notation: A1
        style inside the sheet (A1 … XFD1048576) or R1C1 style (R1C1, RC, R2C,
        the bare R and C). Excel refuses these as table names. A column or
        row past the sheet's edge is no reference, and Excel accepts it —
        XFE1, A1048577 (measured 2026-11-18)."""
        rows, cols = ExcelMixin._EXCEL_SHEET_ROWS, ExcelMixin._EXCEL_SHEET_COLS
        m = _A1_REF.match(name)
        if m and ExcelMixin._excel_col_number(m.group(1)) <= cols \
                and 1 <= int(m.group(2)) <= rows:
            return True
        m = _R1C1_REF.match(name) if name else None
        if m:
            row, col = m.group(1), m.group(2)
            return ((not row or 1 <= int(row) <= rows)
                    and (not col or 1 <= int(col) <= cols))
        return False

    @staticmethod
    def _excel_table_name_problem(name):
        """Why Excel would refuse `name` for a table, worded to finish the
        sentence "'X' cannot be a table name — …", or None when it is fine.

        Checked HERE, before a table exists, because of how xlwings names
        one: it creates the table and only then sets the name, so a refused
        name leaves a table called Table4 behind (seen 2026-11-18, with a
        duplicate name). And the name is later set through display_name,
        which is where Excel applies these rules — the plain .Name property
        takes anything and quietly gives the table a DIFFERENT name for
        formulas ('Has Space' → Has_Space, 'A1' → A1_).

        The rules are Excel's for any defined name, each confirmed against
        the display-name setter that day: a letter, underscore or backslash
        first; then letters, digits, underscores, periods (backslashes and
        question marks too); no spaces; not a cell reference; 255 characters
        at most."""
        if not name:
            return "it is empty"
        if len(name) > 255:
            return f"it is {len(name)} characters long (255 at most)"
        if any(ch.isspace() for ch in name):
            return "it contains a space (join the words with an underscore)"
        if not (name[0].isalpha() or name[0] in "_\\"):
            return "it must start with a letter or an underscore"
        bad = [ch for ch in dict.fromkeys(name)
               if not (ch.isalnum() or ch in "_.\\?")]
        if bad:
            return ("it contains " + " ".join(bad) + " — only letters, digits, "
                    "underscores and periods are allowed")
        if ExcelMixin._excel_is_cell_reference(name):
            return "it reads as a cell reference"
        return None

    @staticmethod
    def _excel_table_line(info):
        """One table as excel_table reports it: a headline (name, where,
        size, style, the display options that are on) and its columns as
        LETTER=header pairs, so a column can be addressed by its header name
        instead of a remembered letter. `info` is _excel_table_info's dict."""
        name = info["name"]
        if info.get("display_name") and info["display_name"] != name:
            # Only for a table named through .Name by something else: the
            # name formulas must use is then not the one the table shows.
            name += f" (in formulas: {info['display_name']})"
        n = info["data_rows"]
        head = (f"• {name} — {info['sheet']}!{info['range']}, "
                f"{n} data row{'' if n == 1 else 's'}, "
                + (f"style {info['style']}" if info["style"] else "no style"))
        if info["options"]:
            head += "; " + ", ".join(info["options"])
        cols = info["columns"]
        if cols is None:
            body = "(header row hidden)"
        else:
            shown = ExcelMixin._EXCEL_TABLE_COLS_SHOWN
            body = ", ".join(f"{letter}={header}" for letter, header in cols[:shown])
            if len(cols) > shown:
                body += f", … +{len(cols) - shown} more"
        return head + "\n  columns: " + body

    @staticmethod
    def _excel_given(value):
        """Whether a numeric tool parameter was supplied — 0 is a value
        here (rows=0 unfreezes), so the usual `or default` test will not do."""
        return value is not None and value != ""

    @staticmethod
    def _excel_whole(value, what, hint):
        """A numeric tool parameter → int. Takes what the providers send for
        a JSON number — 8, 8.0, '8' — and raises a ValueError that names the
        parameter for anything else, a fraction included: Excel would take
        one and quietly drop the fraction (a zoom of 85.6 became 85)."""
        problem = f"'{what}' must be a whole number ({hint}), got {value!r}"
        if isinstance(value, bool):
            raise ValueError(problem)
        try:
            num = float(value)
        except (TypeError, ValueError):
            raise ValueError(problem) from None
        if not _math.isfinite(num) or num != int(num):
            raise ValueError(problem)
        return int(num)

    @staticmethod
    def _excel_pane_count(value, what, limit):
        """excel_freeze_panes' rows / columns → a whole number from 0 to
        limit-1 (one row or column has to stay scrollable)."""
        count = ExcelMixin._excel_whole(value, what, "0 = none")
        if count < 0:
            raise ValueError(f"'{what}' cannot be negative (0 = none)")
        if count >= limit:
            raise ValueError(f"'{what}' must be below {limit:,} — a sheet has "
                             f"{limit:,}, and one has to stay scrollable")
        return count

    @staticmethod
    def _excel_zoom_percent(value):
        """excel_zoom's percent → a whole number in Excel's range. Checked
        before Excel is touched: outside 10-400 Excel's own refusal is the
        bare "Unable to set the Zoom property of the Window class"."""
        low, high = ExcelMixin._EXCEL_ZOOM_MIN, ExcelMixin._EXCEL_ZOOM_MAX
        percent = ExcelMixin._excel_whole(value, "percent", f"{low} to {high}")
        if not low <= percent <= high:
            raise ValueError(f"'percent' must be from {low} to {high}, got {percent}")
        return percent

    @staticmethod
    def _excel_sheet_name_problem(name):
        """Why Excel would refuse `name` for a sheet, worded to finish the
        sentence "'X' cannot be a sheet name — …", or None when it is fine.

        Checked before a sheet is added, for the reason a table's name is
        (_excel_table_name_problem): xlwings adds the sheet and only then
        names it, so a refused name left a `Sheet5` behind (2026-11-18).
        The rules are what Excel answered that day: 31 characters at most;
        none of \\ / ? * [ ] : ; no apostrophe first or last; and not
        History, which it reserves. Spaces, digits, punctuation and a name
        that reads as a cell are all fine."""
        if not name:
            return "it is empty"
        limit = ExcelMixin._EXCEL_SHEET_NAME_MAX
        if len(name) > limit:
            return f"it is {len(name)} characters long ({limit} at most)"
        bad = [ch for ch in dict.fromkeys(name) if ch in ExcelMixin._EXCEL_SHEET_NAME_BAD]
        if bad:
            return ("it contains " + " ".join(bad)
                    + " — none of \\ / ? * [ ] : is allowed")
        if name[0] == "'" or name[-1] == "'":
            return "it begins or ends with an apostrophe"
        if name.lower() == "history":
            return "History is a name Excel reserves"
        return None

    @staticmethod
    def _excel_sheet_where(params):
        """excel_sheet's 'before' / 'after' → ("before" | "after", the other
        sheet's name), or (None, None) when neither is given. Both at once
        is a ValueError: they would name two places."""
        before = (params.get("before") or "").strip()
        after = (params.get("after") or "").strip()
        if before and after:
            raise ValueError("give 'before' or 'after', not both")
        if before:
            return "before", before
        return ("after", after) if after else (None, None)

    @staticmethod
    def _excel_sheet_in_place(names, name, how, ref):
        """Whether `name` sits directly before / after `ref` in `names`, the
        workbook's sheets in tab order. False too when either is missing —
        a sheet that has LEFT the workbook is not in place."""
        if name not in names or ref not in names:
            return False
        return names.index(name) - names.index(ref) == (-1 if how == "before" else 1)

    @staticmethod
    def _excel_sheet_order(names, name, how, ref):
        """The tab order a placement asks for: `names` with `name` directly
        before / after `ref` and every OTHER sheet where it was. `name` may
        be new to the list (add) or already in it (move). This is what the
        read-back is held to — beside the right sheet is not enough: with a
        chart sheet ahead, xlwings' own "add after" swapped two other sheets
        and left the new one beside the sheet named (2026-11-18)."""
        rest = [n for n in names if n != name]
        at = rest.index(ref) + (how == "after")
        return rest[:at] + [name] + rest[at:]

    @staticmethod
    def _excel_pane_words(rows, cols):
        """'top 8 rows and left column' — the frozen part, in words."""
        parts = []
        if rows:
            parts.append("top row" if rows == 1 else f"top {rows} rows")
        if cols:
            parts.append("left column" if cols == 1 else f"left {cols} columns")
        return " and ".join(parts)

    @staticmethod
    def _excel_pane_text(state):
        """A pane state (_excel_pane_state's dict) in words."""
        rows, cols = state["rows"], state["columns"]
        if state["frozen"]:
            words = ExcelMixin._excel_pane_words(rows, cols)
            return f"frozen — {words} kept in view" if words else "frozen"
        parts = ([f"below row {rows}"] if rows else []) + (
            [f"right of column {ExcelMixin._excel_col_letter(cols)}"] if cols else [])
        if parts:
            return "split but not frozen (" + ", ".join(parts) + ")"
        return "not frozen"

    @staticmethod
    def _excel_pane_landed(state, rows, cols):
        """Whether Excel's read-back says the freeze just made is the one
        asked for: the frozen flag right, no split on an axis that was not
        asked for, and each asked-for axis reading the count asked for — or
        one less, which is how Excel reads a split lying past what the
        window can show (39 for 40 rows, 2026-11-18).

        A check and not a formality, because the one failure seen is
        silent: an absurd count raises nothing, Excel just substitutes a
        freeze in the middle of the window (1,048,575 rows became 16 rows by
        10 columns). It can be this strict only because
        _excel_pane_blocked has already refused the case where Excel
        legitimately reads back MORE — hidden rows directly under the split."""
        if not (rows or cols):
            return (not state["frozen"]
                    and state["rows"] == 0 and state["columns"] == 0)

        def axis(wanted, got):
            return got == 0 if wanted == 0 else got >= 1 and got in (wanted, wanted - 1)

        return (bool(state["frozen"])
                and axis(rows, state["rows"]) and axis(cols, state["columns"]))

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

    @staticmethod
    def _excel_reason(e, fallback="Excel gave no reason"):
        """_excel_err's text for use INSIDE a sentence, or `fallback` when
        there is none to give: a COM error without a description — Excel's
        answer to an unknown table style, or to a duplicate table name —
        prints as a bare tuple of numbers, which tells the model nothing."""
        text = ExcelMixin._excel_err(e).strip().rstrip(".")
        return fallback if not text or text.startswith("(") else text

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
    def _excel_book_path(book):
        """An open workbook's full path (a URL for a cloud-hosted one), or
        None when it has none (a new, never-saved Book1) or none can be read."""
        try:
            full = str(book.fullname or "")
        except Exception:
            return None
        return full if (os.path.dirname(full) or "://" in full) else None

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
        want_stem, want_ext = os.path.splitext(want)
        # An exact name first — a single pass matching name OR stem returned
        # whichever came first, so "Accounts.xlsm" could act on an open
        # Accounts.xlsx. The stem alone serves only a name given without an
        # extension ("Accounts"); a DIFFERENT extension never matches.
        for b in books:
            if b.name.lower() == want:
                return b
        if not want_ext:
            for b in books:
                if os.path.splitext(b.name.lower())[0] == want_stem:
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

    # ── Sheet position ───────────────────────────────────────────────────

    def _excel_structure_locked(self, book):
        """True when the workbook's structure is protected: no sheet can be
        added or moved then, and Excel's refusal of a move says nothing."""
        return bool(self._excel_api_bool(book, "ProtectStructure", "protect_structure"))

    @staticmethod
    def _excel_sheet_native_move(book, sht, how, ref):
        """Move `sht` directly before / after `ref` — the sheet tool's one
        native call: xlwings can place a NEW sheet and has nothing for an
        existing one.

        On Windows exactly ONE form of Worksheet.Move is used: a real sheet
        as the first, positional argument (Before). Measured 2026-11-18, in
        early- and late-bound COM alike:
          * A Move with no valid target does not fail — Excel moves the sheet
            OUT of the workbook, into a new one. Leaving Before empty to pass
            After by position did exactly that, every time; After by name did
            it once in a late-bound call, and is what xlwings' own source
            calls "broken in certain environments".
          * So "after R" is "before R's follower", and after the LAST tab it
            is two moves: the sheet before R, then R before the sheet.
          * R's follower is looked up among ALL the workbook's sheets, not
            its worksheets: Worksheet.Index counts chart sheets too.
        macOS uses AppleScript's `move … to before / after`, taken from
        xlwings' terminology table and NOT yet run on a Mac."""
        if sys.platform == "darwin":
            sht.api.move(to=ref.api.before if how == "before" else ref.api.after)
            return
        moved, target = sht.api, ref.api
        if how == "before":
            moved.Move(target)
            return
        every = book.api.Sheets
        position = int(target.Index)
        if position < int(every.Count):
            follower = every(position + 1)
            if str(follower.Name) != sht.name:
                moved.Move(follower)
        else:
            moved.Move(target)
            target.Move(moved)

    @staticmethod
    def _excel_sheet_index_off(book, ref):
        """True when `ref`'s Worksheet.Index is not its place among the
        worksheets: a chart sheet — any tab that is not a worksheet — lies
        ahead of it. xlwings' Windows `sheets.add(after=…)` works the place
        out from that Index over a collection that holds worksheets only.
        Measured 2026-11-18 with one chart sheet ahead of A, B, C, D: after A
        and after B the new sheet landed a tab late, and after C it landed
        before C's old place with C and D swapped. macOS adds by reference,
        with no arithmetic."""
        if sys.platform == "darwin":
            return False
        names = [s.name for s in book.sheets]
        return int(ref.api.Index) != names.index(ref.name) + 1

    def _excel_sheet_put(self, book, sht, how, ref, want):
        """Put `sht` directly before / after `ref`. Returns (whether the
        workbook's sheets are now in the order `want`, the order as read).

        The workbook is brought forward first, because in a workbook that
        is NOT the active one Excel works the landing place out as if that
        workbook's ACTIVE sheet were the one leaving: it allows for the
        removal of the active sheet, not of the moved one, so the sheet
        lands one tab off whenever the two lie on opposite sides of the
        destination — and a move that should change nothing changes
        something. Measured 2026-11-18 over every active sheet, moved sheet
        and place among four sheets: 22 of this method's 96 moves wrong
        "behind", each exactly where that rule puts it, and 96 of 96 right
        "in front" (docs/excel-table-freeze-panes-2026-11-18.md).
        The caller puts the view back. The order is read back WHOLE — the
        sheet beside the one named and every other sheet where it was
        (_excel_sheet_order) — and a miss is tried once more: another
        workbook coming forward between the two calls is the one way left
        to get one."""
        name = sht.name
        names = []
        for _attempt in (1, 2):
            book.activate()
            self._excel_sheet_native_move(book, sht, how, ref)
            names = [s.name for s in book.sheets]
            if name not in names or names == want:
                break
        return names == want, names

    def _excel_sheet_add(self, book, name, params):
        how, ref_name = self._excel_sheet_where(params)
        # The name is settled before a sheet exists (_excel_sheet_name_problem).
        problem = self._excel_sheet_name_problem(name)
        if problem:
            return (f"excel_sheet error: '{name}' cannot be a sheet name — {problem}. "
                    "Nothing was added.")
        existing = [s.name for s in book.sheets]
        taken = self._excel_resolve_sheet_name(existing, name)
        if taken is not None:
            return (f"excel_sheet error: {book.name} already has a sheet named "
                    f"'{taken}'. Nothing was added. Sheets: " + ", ".join(existing))
        ref = (self._excel_get_sheet(book, ref_name) if how
               else book.sheets[book.sheets.count - 1])
        if self._excel_structure_locked(book):
            return (f"excel_sheet error: the structure of {book.name} is protected, so "
                    "no sheet can be added — unprotect the workbook first.")
        side = how or "after"           # no place asked for: after the last sheet
        want = self._excel_sheet_order(existing, name, side, ref.name)
        front, _shown = self._excel_view_mark(book)
        try:
            # xlwings' "after" is not used where a chart sheet lies ahead of
            # `ref` (_excel_sheet_index_off): the sheet is added BEFORE it — a
            # plain Sheets.Add, no arithmetic — and moved after it below.
            if side == "before" or self._excel_sheet_index_off(book, ref):
                new = book.sheets.add(name, before=ref)
            else:
                new = book.sheets.add(name, after=ref)
            names = [s.name for s in book.sheets]
            if names != want:
                _placed, names = self._excel_sheet_put(book, new, side, ref, want)
            if book.sheets.active.name != name:
                new.activate()      # a new sheet is the sheet in view, as in Excel itself
        finally:
            # ... of ITS workbook: which workbook is in front stays as it was.
            self._excel_view_restore(book, (front, None))
        out = (f"Added sheet '{name}' to {book.name}"
               + (f" {how} '{ref.name}'" if how else "")
               + ". Sheets: " + ", ".join(names))
        if names != want:
            if self._excel_sheet_in_place(names, name, side, ref.name):
                out += ("\n⚠ The other sheets are no longer in the order they were — "
                        "the list shows the order now.")
            else:
                out += (f"\n⚠ It is not {side} '{ref.name}': Excel left it where the "
                        "list shows — action=move can put it right.")
        return out

    def _excel_sheet_move(self, book, sht, params):
        how, ref_name = self._excel_sheet_where(params)
        if not how:
            return ("excel_sheet error: 'before' or 'after' is required for move — the "
                    "name of the sheet to put this one next to.")
        ref = self._excel_get_sheet(book, ref_name)
        name = sht.name
        if ref.name == name:
            return f"excel_sheet error: sheet '{name}' cannot be moved {how} itself."
        names = [s.name for s in book.sheets]
        want = self._excel_sheet_order(names, name, how, ref.name)
        if names == want:
            # No Move for a sheet that is there: behind, Excel moves one anyway.
            return (f"Sheet '{name}' is already {how} '{ref.name}' in {book.name} — "
                    "nothing to do. Sheets: " + ", ".join(names))
        if self._excel_structure_locked(book):
            return (f"excel_sheet error: the structure of {book.name} is protected, so "
                    "no sheet can be moved — unprotect the workbook first.")
        mark = self._excel_view_mark(book)
        try:
            placed, names = self._excel_sheet_put(book, sht, how, ref, want)
        finally:
            self._excel_view_restore(book, mark)
        if name not in names:
            return (f"excel_sheet error: sheet '{name}' is no longer in {book.name} — "
                    "Excel moved it OUT, into a new workbook, which is what it does "
                    "with a move that has no target. Look for an unsaved workbook "
                    "holding it. Sheets left: " + ", ".join(names))
        if not placed:
            if self._excel_sheet_in_place(names, name, how, ref.name):
                return (f"excel_sheet error: sheet '{name}' is {how} '{ref.name}' in "
                        f"{book.name}, but the other sheets are no longer in the order "
                        "they were. Sheets now: " + ", ".join(names))
            return (f"excel_sheet error: sheet '{name}' did not end up {how} "
                    f"'{ref.name}' in {book.name}. Sheets now: " + ", ".join(names))
        return (f"Moved sheet '{name}' {how} '{ref.name}' in {book.name}. Sheets: "
                + ", ".join(names))

    # ── Tables ───────────────────────────────────────────────────────────

    def _excel_tables(self, book, sheet=None):
        """Every table of the workbook — or of one sheet — as (sheet, table)
        pairs, in sheet order."""
        sheets = [self._excel_get_sheet(book, sheet)] if sheet else list(book.sheets)
        return [(sht, tbl) for sht in sheets for tbl in sht.tables]

    @staticmethod
    def _excel_table_names(tbl):
        """A table's two names: (.name, the display name formulas use). They
        are the same string for any table named through this tool; they
        differ for one whose .Name was set to something Excel had to adapt."""
        name = tbl.name
        try:
            shown = tbl.display_name or name
        except Exception:
            shown = name
        return name, shown

    def _excel_find_table(self, book, name, sheet=None):
        """Resolve a table by name anywhere in the workbook — a table name is
        unique in it, so no sheet is needed — by the rule sheets get: exact
        first, then case-insensitive; .name first, then the display name."""
        pairs = self._excel_tables(book, sheet)
        named = [self._excel_table_names(tbl) for _sht, tbl in pairs]
        for which in (0, 1):
            candidates = [names[which] for names in named]
            real = self._excel_resolve_sheet_name(candidates, name)
            if real is not None:
                return pairs[candidates.index(real)]
        where = f"sheet '{sheet}' of {book.name}" if sheet else book.name
        listed = ", ".join(names[0] for names in named) or "none"
        raise RuntimeError(f"Table '{name}' not found in {where}. Tables: {listed}")

    def _excel_table_clash(self, book, name, own=None):
        """What in the workbook already answers to `name`, as a sentence — or
        None. A table name must be unique among the tables AND the defined
        names, whatever the case; Excel's own refusal of a duplicate says
        nothing at all (a bare 0x800A03EC). `own` is the table being
        renamed, which may of course keep its name."""
        want = name.lower()
        for sht, tbl in self._excel_tables(book):
            names = self._excel_table_names(tbl)
            if own is not None and names[0] == own:
                continue
            if want in (n.lower() for n in names):
                return (f"a table named '{names[0]}' already exists "
                        f"({sht.name}!{tbl.range.address.replace('$', '')})")
        try:
            for defined in book.names:
                if str(defined.name).lower() == want:
                    return f"the workbook already has a defined name '{defined.name}'"
        except Exception:
            pass    # names that cannot be read are no reason to refuse; Excel still decides
        return None

    def _excel_table_info(self, sht, tbl):
        """One table's facts as a plain dict (see _excel_table_line). Every
        read that a legitimate table can fail is guarded: a table with no
        style has no style NAME to read, one with its header row switched off
        has no header range, an empty one has no data range."""
        name, shown = self._excel_table_names(tbl)
        try:
            style = tbl.table_style or None
        except Exception:
            style = None
        try:
            header = tbl.header_row_range
        except Exception:
            header = None
        columns = None
        if header is not None:
            values = self._excel_as_2d(header.value)[0]
            first = header.column
            columns = [(self._excel_col_letter(first + i), self._excel_cell_str(v))
                       for i, v in enumerate(values)]
        try:
            body = tbl.data_body_range
            data_rows = body.rows.count if body is not None else 0
        except Exception:
            data_rows = 0
        options = []
        for attr, words, _text in self.TABLE_OPTIONS.values():
            try:
                if getattr(tbl, attr):
                    options.append(words)
            except Exception:
                pass
        return {"name": name, "display_name": shown, "sheet": sht.name,
                "range": tbl.range.address.replace("$", ""), "style": style,
                "columns": columns, "data_rows": data_rows, "options": options}

    def _excel_table_set_options(self, tbl, params):
        """Apply the display options the call names (an absent one is left
        alone). Returns (what was set, what Excel refused) — each is tried
        on its own, so one refusal does not hide the others."""
        done, refused = [], []
        for param, (attr, _words, _text) in self.TABLE_OPTIONS.items():
            if params.get(param) is None:
                continue
            value = bool(params[param])
            try:
                setattr(tbl, attr, value)
                done.append(f"{param}={str(value).lower()}")
            except Exception as e:
                refused.append(f"{param} ({self._excel_reason(e)})")
        return done, refused

    @staticmethod
    def _excel_table_unlist(tbl):
        """Turn a table back into plain cells. xlwings has no call for it, so
        this is the table tool's one native call: ListObject.Unlist on
        Windows (verified 2026-11-18 — the values stay, the style's look stays
        as ordinary cell formatting, the name and the filter buttons go),
        and on macOS the AppleScript `convert to range` command, taken from
        xlwings' terminology table and NOT yet run on a Mac."""
        if sys.platform == "darwin":
            tbl.api.convert_to_range()
        else:
            tbl.api.Unlist()

    # ── Panes ────────────────────────────────────────────────────────────

    @staticmethod
    def _excel_win_get(win, prop):
        com, mac = ExcelMixin._EXCEL_PANE_PROPS[prop]
        if sys.platform == "darwin":
            return getattr(win, mac).get()
        return getattr(win, com)

    @staticmethod
    def _excel_win_set(win, prop, value):
        com, mac = ExcelMixin._EXCEL_PANE_PROPS[prop]
        if sys.platform == "darwin":
            getattr(win, mac).set(value)
        else:
            setattr(win, com, value)

    @staticmethod
    def _excel_view_mark(book):
        """What is on screen before a tool has to show another sheet: (the
        active workbook's name, this workbook's active sheet's name) — None
        for one that cannot be read. _excel_view_restore puts it back."""
        try:
            active_book = book.app.books.active.name
        except Exception:
            active_book = None
        try:
            active_sheet = book.sheets.active.name
        except Exception:
            active_sheet = None
        return active_book, active_sheet

    @staticmethod
    def _excel_view_restore(book, mark):
        """Put the active sheet and the active workbook back as
        _excel_view_mark found them. Both are checked, because showing a
        sheet of a workbook that is not in front sometimes brings that
        workbook forward and sometimes does not (seen both ways 2026-11-18).
        Best-effort: a view that cannot be restored never fails the tool."""
        active_book, active_sheet = mark
        try:
            if active_sheet and book.sheets.active.name != active_sheet:
                book.sheets[active_sheet].activate()
        except Exception:
            pass
        try:
            if active_book and book.app.books.active.name != active_book:
                book.app.books[active_book].activate()
        except Exception:
            pass

    def _excel_pane_window(self, book, sht):
        """The window whose view of `sht` — its panes, its zoom — is to be
        read or set, with `sht` showing in it.

        Both belong to a (window, sheet) pair and can only be read or set
        for the sheet a window currently shows — so the sheet is shown first
        (the caller restores the view afterwards).

        On Windows this is the WORKBOOK'S OWN window, never the
        application's active one: a freeze through ActiveWindow lands on
        whatever is in front at that instant, and the agent is attached to
        the user's Excel — another workbook coming forward mid-call would
        have ITS sheet frozen. The workbook's window needs neither the
        workbook active nor Excel visible (verified 2026-11-18: another
        workbook stayed in front and its own panes stayed untouched). What
        the window shows is checked rather than assumed, because activating
        a HIDDEN sheet raises nothing and changes nothing.

        macOS's AppleScript reaches a window only as the application's
        active one, so there the workbook is brought forward and both it and
        the sheet are checked before the window is handed out. NOT yet run
        on a Mac."""
        if sys.platform == "darwin":
            book.activate()
            if book.sheets.active.name != sht.name:
                sht.activate()
            app = book.app
            if app.books.active.name != book.name or book.sheets.active.name != sht.name:
                raise RuntimeError(
                    f"could not bring sheet '{sht.name}' of {book.name} to the "
                    "front — its panes can only be reached while it is showing")
            return app.api.active_window
        if book.sheets.active.name != sht.name:
            sht.activate()
        win = book.api.Windows(1)
        if str(win.ActiveSheet.Name) != sht.name:
            raise RuntimeError(
                f"could not show sheet '{sht.name}' in {book.name}'s window (is "
                "it hidden?) — its panes can only be reached while it is showing")
        return win

    def _excel_pane_state(self, win):
        """What Excel reports for a window's panes. 'rows' / 'columns' are
        its raw SplitRow / SplitColumn: where the split IS, which is not
        always how many rows are visibly frozen (_excel_pane_true)."""
        get = self._excel_win_get
        return {"frozen": bool(get(win, "frozen")),
                "rows": int(get(win, "split_row") or 0),
                "columns": int(get(win, "split_column") or 0),
                "scroll_row": int(get(win, "scroll_row") or 1),
                "scroll_column": int(get(win, "scroll_column") or 1)}

    def _excel_pane_apply(self, win, rows, cols):
        """Freeze `rows` top rows and `cols` left columns (0, 0 = unfreeze).

        The order matters, each step measured 2026-11-18. Unfreezing alone
        leaves an ordinary split behind, so the split is cleared as well.
        And a freeze is taken from the window's CURRENT scroll position —
        asked for 8 rows while scrolled to row 50, Excel froze rows 50-57 and
        put rows 1-49 out of reach — so the window is scrolled to the top-left
        corner before the split is set."""
        put = self._excel_win_set
        put(win, "frozen", False)
        put(win, "split_row", 0)
        put(win, "split_column", 0)
        put(win, "scroll_row", 1)
        put(win, "scroll_column", 1)
        if rows or cols:
            put(win, "split_column", cols)
            put(win, "split_row", rows)
            put(win, "frozen", True)

    def _excel_pane_scroll(self, win, before, rows, cols):
        """Scroll the unfrozen part back to where the user had it, after
        _excel_pane_apply sent the window to its top-left corner. With frozen
        panes ScrollRow / ScrollColumn address the scrollable part only, so
        this never undoes a freeze.

        Only a position the user had actually scrolled TO is put back. A
        window with two frozen columns reads ScrollColumn 3 while it shows
        its leftmost columns; put back as a bare number after a freeze with
        no frozen columns, that 3 scrolled columns A and B out of view
        (2026-11-18). Best-effort: the view is a courtesy, never a reason
        for the tool to fail."""
        try:
            for key, frozen_before, frozen_now in (
                    ("scroll_row", before["rows"], rows),
                    ("scroll_column", before["columns"], cols)):
                home = (frozen_before if before["frozen"] else 0) + 1
                if before[key] > max(home, frozen_now + 1):
                    self._excel_win_set(win, key, before[key])
        except Exception:
            pass

    @staticmethod
    def _excel_span_hidden(sht, first, last, axis):
        """True when every row (axis 'row') or column from `first` to `last`
        is hidden — one call: a range's row_height / column_width is 0 when
        all of it is hidden, and None when it is mixed (verified 2026-11-18,
        for hidden and for filtered-out rows alike)."""
        if axis == "row":
            return sht.range((first, 1), (last, 1)).row_height == 0
        return sht.range((1, first), (1, last)).column_width == 0

    def _excel_page_layout(self, win):
        """True when the window shows its sheet in Page Layout view (COM's
        xlPageLayoutView is 3; appscript answers with a keyword naming it).
        A view that cannot be read counts as not being one."""
        try:
            view = self._excel_win_get(win, "view")
        except Exception:
            return False
        return view == 3 or "page_layout" in str(view).lower()

    def _excel_pane_blocked(self, sht, win, rows, cols):
        """Why this freeze must not be attempted, as a sentence — or None.

        1. Page Layout view has no panes: a freeze there fails half-way,
           after the panes the sheet had are already gone.
        2. The row directly below the rows to freeze (or the column right of
           the columns) is hidden. Excel positions a split in points and a
           hidden row has no height, so with rows 9-20 hidden "below row 8"
           and "below row 20" are one position — and Excel settles on the far
           end. The freeze LOOKS right and is wrong: the hidden rows are
           inside it, and stay frozen once they are shown. Measured
           2026-11-18: one header row frozen over a filtered table was 31
           frozen rows when the filter was cleared, and Excel's own
           freeze-at-the-active-cell route does the same. A freeze made
           FIRST and filtered afterwards is stable, so the remedy is the
           order — show the rows, then freeze.

        Visibility that cannot be read does not block: _excel_pane_landed
        then catches a freeze that came out larger than asked."""
        if self._excel_page_layout(win):
            return ("the sheet is in Page Layout view, which has no panes — "
                    "switch it to Normal view first")
        for count, axis in ((rows, "row"), (cols, "column")):
            if not count:
                continue
            try:
                hidden = self._excel_span_hidden(sht, count + 1, count + 1, axis)
            except Exception:
                hidden = False
            if not hidden:
                continue
            if axis == "row":
                return (f"row {count + 1}, directly below the "
                        f"{'row' if count == 1 else f'{count} rows'} to freeze, is "
                        "hidden (filtered out?). Excel would take the hidden rows "
                        "into the frozen part, where they stay frozen once shown "
                        "again — show them (clear the filter) first, then freeze")
            return (f"column {self._excel_col_letter(count + 1)}, directly right of "
                    f"the {'column' if count == 1 else f'{count} columns'} to freeze, "
                    "is hidden. Excel would take the hidden columns into the frozen "
                    "part, where they stay frozen once shown again — show them "
                    "first, then freeze")
        return None

    def _excel_pane_home(self, win, rows, cols):
        """Whether a window's frozen part begins at the sheet's first row /
        column: True, False, or None where it cannot be told. A freeze made
        while scrolled keeps the WRONG rows in view (_excel_pane_apply) yet
        reads exactly like a proper one — same flag, same split — and only
        the frozen pane's own scroll position gives it away. That is reached
        through the window's panes, which COM exposes and the AppleScript
        route used here does not."""
        if sys.platform == "darwin":
            return None
        try:
            pane = win.Panes(1)
            return ((not rows or int(pane.ScrollRow) == 1)
                    and (not cols or int(pane.ScrollColumn) == 1))
        except Exception:
            return None

    def _excel_pane_extent(self, sht, split, axis):
        """How many rows (or columns) are VISIBLY frozen, given where Excel
        reports the split. The two differ when the frozen part ends in
        hidden rows — a freeze made over hidden rows takes them in (see
        _excel_pane_blocked), and while they stay hidden Excel reads the
        split one short of its far end: 19 for rows 1-20 with 9-20 hidden.
        The row after the reported split is then hidden too, which is the
        tell; the visible part ends at the last shown row before that hidden
        run, found by bisection (a filtered sheet can hide thousands).

        A freeze made normally and filtered afterwards has its hidden run
        start right after the split, so it reads true: the split itself.
        Whatever cannot be read leaves Excel's own figure standing."""
        if split <= 0:
            return 0
        end = split + 1

        def hidden_from(first):
            return self._excel_span_hidden(sht, first, end, axis)

        try:
            if not hidden_from(end):
                return split
            low, high = 1, end
            while low < high:
                mid = (low + high) // 2
                if hidden_from(mid):
                    high = mid
                else:
                    low = mid + 1
            return low - 1
        except Exception:
            return split

    def _excel_pane_true(self, sht, state):
        """`state` as the user sees it. A frozen state's counts become the
        rows / columns VISIBLY frozen (_excel_pane_extent), and 'buried'
        names any hidden rows or columns lying inside the frozen part beyond
        them — they will show as frozen the moment they are unhidden. An
        unfrozen split is left as read."""
        true = dict(state, buried=[])
        if not state["frozen"]:
            return true
        for key, axis in (("rows", "row"), ("columns", "column")):
            shown = self._excel_pane_extent(sht, state[key], axis)
            if shown < state[key]:
                true["buried"].append(
                    f"rows from row {shown + 1}" if axis == "row" else
                    f"columns from column {self._excel_col_letter(shown + 1)}")
            true[key] = shown
        return true

    def _excel_pane_put_back(self, win, before):
        """After a freeze that failed part-way: restore the panes as
        `before` (a _excel_pane_true state) describes them. True on success."""
        try:
            if before["frozen"]:
                self._excel_pane_apply(win, before["rows"], before["columns"])
            else:
                self._excel_pane_apply(win, 0, 0)
                if before["rows"] or before["columns"]:
                    self._excel_win_set(win, "split_column", before["columns"])
                    self._excel_win_set(win, "split_row", before["rows"])
            self._excel_pane_scroll(win, before, before["rows"] if before["frozen"] else 0,
                                    before["columns"] if before["frozen"] else 0)
            return True
        except Exception:
            return False

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
                    # Excel holds ONE workbook per name, so this may be a
                    # different file: a scratch copy in C:\Temp would attach
                    # to the user's real Budget.xlsx, and every write — and
                    # excel_close's default save — would land in it.
                    open_path = self._excel_book_path(book)
                    if (open_path and "://" not in open_path
                            and os.path.normcase(os.path.abspath(open_path)) != os.path.normcase(ap)):
                        return (f"excel_open error: a DIFFERENT workbook named {book.name} "
                                f"is already open ({open_path}); Excel cannot hold two "
                                f"workbooks with the same name. Close that one first "
                                f"(excel_close), or save this file under another name.")
                    note = f"Attached to already-open {book.name}"
                    if open_path and "://" in open_path:
                        # A cloud-hosted book's path is a URL that cannot be
                        # compared with a local path: say where it lives.
                        note += f" (open from {open_path} — the one workbook of that name)"
                    note += ". "
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
                return self._excel_sheet_add(book, name, params)
            sht = self._excel_get_sheet(book, name)
            if action == "move":
                return self._excel_sheet_move(book, sht, params)
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

    def do_excel_table(self, params):
        try:
            action = (params.get("action") or "").strip().lower()
            # The action first, as in excel_sheet: a bad one is named as such
            # rather than as whatever its placeholder arguments fail on.
            if action not in self.TABLE_ACTIONS:
                return (f"excel_table error: unknown action '{action}'. Use "
                        + ", ".join(self.TABLE_ACTIONS) + ".")
            book = self._excel_book(params.get("workbook"))
            sheet = (params.get("sheet") or "").strip()
            name = (params.get("name") or "").strip()
            if action == "list":
                pairs = self._excel_tables(book, sheet or None)
                where = (f"sheet '{self._excel_get_sheet(book, sheet).name}' of "
                         f"{book.name}") if sheet else book.name
                if not pairs:
                    return f"No tables in {where}."
                return (f"Tables in {where}:\n" + "\n".join(
                    self._excel_table_line(self._excel_table_info(sht, tbl))
                    for sht, tbl in pairs))
            if action == "create":
                return self._excel_table_create(book, sheet, name, params)
            if not name:
                return (f"excel_table error: 'name' is required for '{action}' "
                        "(action=list shows the workbook's tables).")
            sht, tbl = self._excel_find_table(book, name, sheet or None)
            if action == "update":
                return self._excel_table_update(book, sht, tbl, params)
            if action == "convert_to_range":
                real, _shown = self._excel_table_names(tbl)
                where = f"{sht.name}!{tbl.range.address.replace('$', '')}"
                self._excel_table_unlist(tbl)
                # The effect, not the call: the table must be gone from the workbook.
                if any(self._excel_table_names(t)[0] == real
                       for _s, t in self._excel_tables(book)):
                    return (f"excel_table error: Excel still lists table '{real}' "
                            f"({where}) after converting it — nothing changed.")
                return (f"Converted table '{real}' ({where}) in {book.name} back to "
                        "an ordinary range. The cells keep their values and their "
                        "look; the table's name, banding rules and filter buttons "
                        "are gone.")
            # Unreachable, like excel_sheet's: a name added to TABLE_ACTIONS
            # without a branch fails loudly instead of returning None.
            return (f"excel_table error: action '{action}' is accepted but "
                    "not implemented — this is a bug in excel_mixin.")
        except Exception as e:
            return f"excel_table error: {self._excel_err(e)}"

    def _excel_table_create(self, book, sheet, name, params):
        rng_str = (params.get("range") or "").strip()
        if not rng_str:
            return ("excel_table error: 'range' is required for create — the cells "
                    "to turn into a table, header row first (e.g. 'B8:F20').")
        # Everything that can be known about the name is checked before the
        # table exists (see _excel_table_name_problem for why).
        if name:
            problem = self._excel_table_name_problem(name)
            if problem:
                return (f"excel_table error: '{name}' cannot be a table name — "
                        f"{problem}. Nothing was created.")
            clash = self._excel_table_clash(book, name)
            if clash:
                return f"excel_table error: {clash}. Nothing was created."
        sht = self._excel_get_sheet(book, sheet or None)
        style = (params.get("style") or "").strip()
        try:
            rng = sht.range(rng_str)
            asked = rng.address.replace("$", "")
            header_only = rng.rows.count == 1
        except Exception as e:
            reason = self._excel_reason(e, "")
            return (f"excel_table error: '{rng_str}' is not a range of sheet "
                    f"'{sht.name}' that Excel can read"
                    + (f" ({reason})" if reason else "")
                    + " — give it in A1 style, e.g. 'B8:F20'. Nothing was created.")
        try:
            # The style goes in with the creation: Excel refuses an unknown
            # one BEFORE making anything, so a bad style leaves no table.
            tbl = sht.tables.add(source=rng,
                                 table_style_name=style or self._EXCEL_TABLE_STYLE)
        except Exception as e:
            # Excel names most refusals (an overlap, a protected sheet); the
            # one it does not name is the unknown style.
            reason = self._excel_reason(e, "")
            if reason:
                why = f": {reason}."
            elif style:
                why = (" and gave no reason — most likely it does not know the "
                       f"style '{style}'. " + self._EXCEL_TABLE_STYLE_HINT)
            else:
                why = " and gave no reason."
            return (f"excel_table error: Excel could not make a table of "
                    f"{sht.name}!{asked}{why} Nothing was created.")
        notes = []
        if name:
            try:
                # display_name, not name: the setter Excel validates, and the
                # one after which the table's two names are the same string.
                tbl.display_name = name
            except Exception as e:
                notes.append(
                    f"⚠ Excel refused the name '{name}' ({self._excel_reason(e)}), so "
                    "the table keeps its automatic name — rename it with "
                    "action=update or undo it with action=convert_to_range.")
        done, refused = self._excel_table_set_options(tbl, params)
        if refused:
            notes.append("⚠ Not applied: " + "; ".join(refused) + ".")
        info = self._excel_table_info(sht, tbl)
        if info["range"] != asked:
            # The table is not always the range asked for, and the result
            # says so rather than leaving the model to find out: a header
            # row alone gets one empty row for the first entry (a single cell
            # becomes a one-column table the same way), and a Total row is
            # part of the table.
            why = ((["a header row alone gets one empty data row"] if header_only else [])
                   + (["the Total row is part of the table"]
                      if params.get("total_row") else []))
            notes.append(f"The table is {info['range']}, not the {asked} asked for"
                         + (": " + "; ".join(why) if why else "") + ".")
        out = (f"Created table '{info['name']}' in {book.name}"
               + (f" ({', '.join(done)})" if done else "") + ".\n"
               + self._excel_table_line(info))
        return out + ("\n" + "\n".join(notes) if notes else "")

    def _excel_table_update(self, book, sht, tbl, params):
        new_name = (params.get("new_name") or "").strip()
        style = (params.get("style") or "").strip()
        rng_str = (params.get("range") or "").strip()
        if not (new_name or style or rng_str
                or any(params.get(p) is not None for p in self.TABLE_OPTIONS)):
            return ("excel_table error: nothing to update — give at least one of "
                    "new_name, style, range, " + ", ".join(self.TABLE_OPTIONS) + ".")
        old_name, _shown = self._excel_table_names(tbl)
        if new_name:
            # Refused before anything else changes, so an update is never
            # half-applied over a name Excel would not take.
            problem = self._excel_table_name_problem(new_name)
            if problem:
                return (f"excel_table error: '{new_name}' cannot be a table name — "
                        f"{problem}. Nothing was changed.")
            clash = self._excel_table_clash(book, new_name, own=old_name)
            if clash:
                return f"excel_table error: {clash}. Nothing was changed."
        done, refused = [], []
        if rng_str:
            try:
                tbl.resize(sht.range(rng_str))
                done.append("resized to " + tbl.range.address.replace("$", ""))
            except Exception as e:
                refused.append(f"range {rng_str} (" + self._excel_reason(
                    e, "not a range Excel can resize the table to") + ")")
        if style:
            try:
                tbl.table_style = style
                done.append(f"style={style}")
            except Exception as e:
                # An unknown style is refused with no reason at all (pywin32
                # words it "Invalid number of parameters"); the table keeps
                # the style it had.
                refused.append(
                    f"style {style} ("
                    + self._excel_reason(e, "Excel does not know that style")
                    + ". " + self._EXCEL_TABLE_STYLE_HINT + ")")
        set_done, set_refused = self._excel_table_set_options(tbl, params)
        done += set_done
        refused += set_refused
        if new_name:
            try:
                tbl.display_name = new_name
                done.append(f"renamed from '{old_name}'")
            except Exception as e:
                refused.append(f"new_name {new_name} ({self._excel_reason(e)})")
        info = self._excel_table_info(sht, tbl)
        out = (f"Updated table '{info['name']}' in {book.name}"
               + (": " + ", ".join(done) if done else " — nothing was changed") + ".\n"
               + self._excel_table_line(info))
        if refused:
            out += "\n⚠ Not applied: " + "; ".join(refused) + "."
        return out

    def do_excel_freeze_panes(self, params):
        try:
            rows_in, cols_in = params.get("rows"), params.get("columns")
            report_only = not (self._excel_given(rows_in) or self._excel_given(cols_in))
            # Validated before Excel is touched: a count Excel cannot take
            # would otherwise be found out half-way through the sequence.
            rows = (self._excel_pane_count(rows_in, "rows", self._EXCEL_SHEET_ROWS)
                    if self._excel_given(rows_in) else 0)
            cols = (self._excel_pane_count(cols_in, "columns", self._EXCEL_SHEET_COLS)
                    if self._excel_given(cols_in) else 0)
            book, sht = self._excel_target(params)
            where = f"[{book.name}]{sht.name}"
            mark = self._excel_view_mark(book)
            try:
                win = self._excel_pane_window(book, sht)
                before = self._excel_pane_true(sht, self._excel_pane_state(win))
                was = self._excel_pane_text(before)
                if report_only:
                    if self._excel_page_layout(win):
                        return (f"Panes on {where}: none — the sheet is in Page "
                                "Layout view, which has no panes.")
                    out = f"Panes on {where}: {was}."
                    if before["buried"]:
                        out += ("\n⚠ Hidden " + " and ".join(before["buried"]) + " lie "
                                "inside the frozen part and will show as frozen once "
                                "unhidden. If that is not intended: show them, then "
                                "unfreeze and freeze again.")
                    return out
                if not (rows or cols or before["frozen"]
                        or before["rows"] or before["columns"]):
                    return f"Panes on {where} were not frozen — nothing to do."
                if ((rows or cols) and before["frozen"] and not before["buried"]
                        and (before["rows"], before["columns"]) == (rows, cols)
                        and self._excel_pane_home(win, rows, cols)):
                    # Already so, and properly: say it and touch nothing. This
                    # is also what keeps a sheet that was frozen first and
                    # filtered later — the everyday case — from being refused
                    # by the hidden-row rule below for a freeze it already has.
                    return (f"Panes on {where} are already frozen that way "
                            f"({was}) — nothing to do.")
                blocked = self._excel_pane_blocked(sht, win, rows, cols)
                if blocked:
                    return (f"excel_freeze_panes error: {blocked}. Nothing was "
                            f"changed on {where}.")
                problem = None
                try:
                    self._excel_pane_apply(win, rows, cols)
                    after = self._excel_pane_state(win)
                    if not self._excel_pane_landed(after, rows, cols):
                        problem = "Excel reports them " + self._excel_pane_text(after)
                except Exception as e:
                    problem = self._excel_reason(e)
                if problem:
                    wanted = ("freeze the " + self._excel_pane_words(rows, cols)
                              if rows or cols else "unfreeze the panes")
                    put_back = self._excel_pane_put_back(win, before)
                    return (f"excel_freeze_panes error: could not {wanted} of {where}: "
                            f"{problem}. "
                            + (f"The panes were put back as they were ({was})."
                               if put_back else
                               "⚠ The panes could NOT be put back as they were — "
                               "look at the sheet."))
                self._excel_pane_scroll(win, before, rows, cols)
                if not (rows or cols):
                    return f"Unfroze the panes of {where} (before: {was})."
                out = (f"Froze the {self._excel_pane_words(rows, cols)} of "
                       f"{where} (before: {was}).")
                if any(want and got == want - 1 for want, got in
                       ((rows, after["rows"]), (cols, after["columns"]))):
                    # The read-back _excel_pane_landed lets through: worth
                    # saying, because such a sheet can no longer be scrolled.
                    out += ("\n⚠ Excel reads this split one short, which is how it "
                            "reports a frozen part larger than the window shows at "
                            "its present size — what lies beyond it cannot be "
                            "scrolled into view until the window is larger or less "
                            "is frozen.")
                return out
            finally:
                self._excel_view_restore(book, mark)
        except Exception as e:
            return f"excel_freeze_panes error: {self._excel_err(e)}"

    def _excel_zoom_read(self, win):
        """A window's zoom as a whole percentage (COM answers 85.0)."""
        return int(round(float(self._excel_win_get(win, "zoom"))))

    def do_excel_zoom(self, params):
        try:
            asked = params.get("percent")
            report_only = not self._excel_given(asked)
            # Validated before Excel is touched, like the pane counts.
            percent = None if report_only else self._excel_zoom_percent(asked)
            book, sht = self._excel_target(params)
            where = f"[{book.name}]{sht.name}"
            mark = self._excel_view_mark(book)
            try:
                # A zoom, like the panes, belongs to a (window, sheet) pair:
                # the same window, reached the same way, for the same reason.
                win = self._excel_pane_window(book, sht)
                before = self._excel_zoom_read(win)
                # Normal view and Page Layout view each keep a zoom of their
                # own (2026-11-18: 90% set in one left the other at 120%).
                note = (" The sheet is in Page Layout view, which keeps a zoom of its "
                        "own — Normal view's is not this one."
                        if self._excel_page_layout(win) else "")
                if report_only:
                    return f"Zoom on {where}: {before}%.{note}"
                if before == percent:
                    return (f"Zoom on {where} is already {percent}% — nothing to do."
                            + note)
                self._excel_win_set(win, "zoom", percent)
                after = self._excel_zoom_read(win)
                if after != percent:
                    return (f"excel_zoom error: asked for {percent}% on {where}, and "
                            f"Excel reports {after}%.")
                return f"Set the zoom of {where} to {percent}% (before: {before}%).{note}"
            finally:
                self._excel_view_restore(book, mark)
        except Exception as e:
            return f"excel_zoom error: {self._excel_err(e)}"

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
            if app.books.count > 1 and not params.get("workbook"):
                # Without a name this closed the ACTIVE workbook — saved by
                # default — which in the user's attached Excel may be their
                # own: the hazard quit_app's guard exists for, one call early.
                names = ", ".join(b.name for b in app.books)
                return (f"excel_close error: {app.books.count} workbooks are open "
                        f"({names}) — pass 'workbook' to say which one to close.")
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
