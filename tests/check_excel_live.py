"""Live-Excel check for the excel_* tools. Run it by hand, on either OS.

    python tests/check_excel_live.py             # attached to the Excel you have running
    python tests/check_excel_live.py --private   # in an Excel instance of its own

NOT part of `python -m unittest discover` (the filename doesn't match test*.py)
because it needs a running Excel. The pure helpers are unit-tested in
tests/test_excel_mixin.py and tests/test_excel_tables_panes.py; this exercises
the parts that only a real Excel can answer, and asserts the places where
Windows COM and macOS AppleScript are expected to DIFFER.

Safety: every workbook it touches lives in a fresh temp directory, and — since
2026-11-18 — EVERY call names its workbook: a write addressed to "the active
workbook" lands in yours the moment you click on it mid-run. It never closes
"the active workbook" and never quits your Excel. Workbooks you already have
open are only ever read as bystander names.

--private (2026-11-18) goes further: the check starts a hidden Excel instance
of its own and never attaches to yours at all — the way to run it while a
workbook that matters is open. That instance is closed at exit, through raw
COM on Windows: xlwings' own quit() / kill() end with Apps.cleanup(), which
force-kills every Excel process that shows no workbook window. Windows-run;
macOS can start a second instance too, but that path has not been run there.
"""

import atexit
import os
import re
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import myagent.excel_mixin as excel_mixin  # noqa: E402
from myagent.excel_mixin import ExcelMixin  # noqa: E402

IS_WIN = sys.platform == "win32"
RESULTS = []
PRIVATE = "--private" in sys.argv[1:]
PRIVATE_APP = None
if PRIVATE:
    PRIVATE_APP = excel_mixin.xw.App(visible=False, add_book=False)

    def _close_private():
        try:
            for book in list(PRIVATE_APP.books):
                try:
                    book.close()            # never saved: scratch only
                except Exception:
                    pass
            if IS_WIN:
                PRIVATE_APP.api.Quit()      # raw COM — see the module docstring
            else:
                PRIVATE_APP.quit()
        except Exception as e:
            print(f"(private Excel instance not closed cleanly: {e})")

    atexit.register(_close_private)


class Host(ExcelMixin):
    """With --private, the one chokepoint every tool attaches through hands
    out the check's own instance instead of the running one."""

    def _excel_app(self, launch=False):
        if PRIVATE_APP is None:
            return super()._excel_app(launch)
        self._excel_com_init()
        return PRIVATE_APP


h = Host()
# NOT the system temp dir: on macOS that is /var/folders/..., which Excel's
# sandbox refuses to save into (OSERROR -50, and it looks like a path bug).
# A folder in the user's home works on both OSes. Override with a path argument.
_PATHS = [a for a in sys.argv[1:] if not a.startswith("--")]
TMP = (_PATHS[0] if _PATHS
       else os.path.join(os.path.expanduser("~"), "myagent_excel_check"))
os.makedirs(TMP, exist_ok=True)
BOOK = os.path.join(TMP, "check_main.xlsx")
BYSTANDER = os.path.join(TMP, "check_bystander.xlsx")
LAYOUT = os.path.join(TMP, "check_layout.xlsx")
MAIN = "check_main.xlsx"            # named in every call: see Safety above
LAY = "check_layout.xlsx"


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}")
    if detail and not condition:
        print(f"         {str(detail)[:220]}")


def skip(name, why):
    RESULTS.append((name, None, why))
    print(f"  [SKIP] {name} — {why}")


def force_rm(path):
    if os.path.exists(path):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        os.remove(path)


# ── 1. attach / create ──────────────────────────────────────────────────────
print(f"\nworking directory: {TMP}")
if not IS_WIN:
    print("(macOS: Excel may show a one-time 'Grant File Access' dialog for\n"
          " this folder on the first save — click Select/Grant Access.)")
print("\n1. attach and create")
pre = h.do_excel_open({})
print(f"     ({'the private instance' if PRIVATE else 'your Excel'} before we start: "
      f"{pre.splitlines()[0]})")
for p in (BOOK, BYSTANDER, LAYOUT):
    force_rm(p)
out = h.do_excel_open({"path": BOOK, "create": True})
check("create + save a new workbook", "error:" not in out, out)
check("a fresh writable workbook gets NO read-only warning",
      "READ-ONLY" not in out, out)

# ── 2. small write: echo + no false drop warning ────────────────────────────
print("\n2. small write (<=200 cells: echoes values back)")
out = h.do_excel_write({"workbook": MAIN, "start_cell": "A1", "values": [
    ["Item", "Qty", "Unit Price", "Date", "Total"],
    ["Widget", "12", "7.50", "2026-08-01", "=B2*C2"],
    ["Gadget", "3", "19.95", "2026-08-02", "=B3*C3"],
    ["", "", "", "Sum", "=SUM(E2:E3)"],
]})
check("write reports success", "error:" not in out, out[:200])
check("values echoed back (write really landed)", "Widget" in out, out[:300])
check("formula recalculated (=B2*C2 -> 90)", "90" in out, out[:300])
check("no false VERIFICATION FAILED", "VERIFICATION FAILED" not in out, out[:300])

# ── 3. large write: the probe path (no echo, but verified) ──────────────────
print("\n3. large write (>200 cells: probe path, added 2026-08-01)")
big = [[f"r{r}c{c}" for c in range(10)] for r in range(30)]   # 300 cells
out = h.do_excel_write({"workbook": MAIN, "start_cell": "A10", "values": big})
check("large write reports success", "error:" not in out, out[:200])
check("large write is NOT echoed (would swamp the model)",
      "Current values" not in out, out[:200])
check("no false VERIFICATION FAILED on a landed large write",
      "VERIFICATION FAILED" not in out, out[:300])
back = h.do_excel_read({"workbook": MAIN, "range": "A10:C12"})
check("large write really landed", "r0c0" in back, back[:200])

# ── 4. formulas + the currency/Decimal wire-type regression ─────────────────
print("\n4. formulas and the currency wire type")
out = h.do_excel_read({"workbook": MAIN, "range": "E1:E4", "formulas": True})
check("formulas read back as formulas", "=SUM(" in out, out[:200])
h.do_excel_format({"workbook": MAIN, "range": "C2:C3", "number_format": "$#,##0.00"})
out = h.do_excel_read({"workbook": MAIN, "range": "C2:C3"})
# On Windows COM these cells come back as decimal.Decimal, NOT float. This is
# the regression _excel_cell_str exists for; it is a no-op on macOS.
check("currency cells display cleanly (Decimal path)",
      "19.95" in out and "19.9500" not in out, out[:200])
out = h.do_excel_find({"workbook": MAIN, "text": "19.95"})
check("numeric find matches a currency-formatted cell",
      "C3" in out, out[:200])

# ── 5. sheet resolution (rewritten 2026-08-01) ──────────────────────────────
print("\n5. sheet resolution")
h.do_excel_sheet({"workbook": MAIN, "action": "add", "name": "Data Sheet"})
out = h.do_excel_sheet({"workbook": MAIN, "action": "activate", "name": "NoSuchSheet"})
check("missing sheet -> friendly message, not a raw COM/OSERROR",
      "not found" in out and "Sheets:" in out, out[:200])
check("missing sheet error does not leak a driver error",
      "OSERROR" not in out and "0x8" not in out, out[:200])
out = h.do_excel_write({"workbook": MAIN, "sheet": "data sheet", "start_cell": "A1",
                        "values": [["case-insensitive"]]})
check("case-insensitive sheet lookup", "error:" not in out, out[:200])
out = h.do_excel_sheet({"workbook": MAIN, "action": "frobnicate", "name": "Data Sheet"})
check("unknown action is named as such (not 'sheet not found')",
      "unknown action" in out, out[:200])
h.do_excel_sheet({"workbook": MAIN, "action": "delete", "name": "Data Sheet"})

# ── 6. run_macro: the platforms are EXPECTED to differ ──────────────────────
print("\n6. run_macro on a macro-free workbook")
out = h.do_excel_run_macro({"workbook": MAIN, "macro": "NoSuchMacro"})
if IS_WIN:
    # COM raises for an unknown macro, so the mixin keeps the confident path.
    check("Windows: a missing macro ERRORS (never reported as 'ran')",
          "error:" in out, out[:250])
else:
    check("macOS: a missing macro is reported as 'dispatched', not 'ran'",
          "dispatched" in out and "NOTE" in out, out[:250])

# ── 7. read-only detection ─────────────────────────────────────────────────
print("\n7. read-only detection (silent failure -> up-front warning)")
h.do_excel_save({"workbook": "check_main.xlsx"})
h.do_excel_close({"workbook": "check_main.xlsx", "save": True})
# Deliberately a SEPARATE workbook. Excel sometimes keeps reporting a path as
# read-only for the rest of the session even after the file is made writable
# again (seen live 2026-08-01; it does not reproduce every run). Reusing the
# main workbook here would poison checks 8 and 9 with that stale state.
RO = os.path.join(TMP, "check_readonly.xlsx")
force_rm(RO)
h.do_excel_open({"path": RO, "create": True})
h.do_excel_close({"workbook": "check_readonly.xlsx", "save": True})
if not os.path.exists(RO):
    for n in ("read-only open is flagged at open time",
              "read-only note lists what to check",
              "closing a read-only workbook says edits were NOT saved"):
        skip(n, "workbook never reached disk (see check 1)")
else:
    os.chmod(RO, stat.S_IREAD)        # read-only flag on Windows, 0444 on POSIX
    out = h.do_excel_open({"path": RO})
    check("read-only open is flagged at open time", "READ-ONLY" in out, out)
    check("read-only note lists what to check", "Check:" in out, out)
    out = h.do_excel_close({"workbook": "check_readonly.xlsx", "save": True})
    check("closing a read-only workbook says edits were NOT saved",
          "NOT SAVED" in out and "error:" not in out, out)
    os.chmod(RO, stat.S_IWRITE | stat.S_IREAD)

# ── 8. open kwargs ─────────────────────────────────────────────────────────
print("\n8. open parameters")
h.do_excel_close({"workbook": "check_main.xlsx", "save": False})
if not os.path.exists(BOOK):
    skip("extra open params are harmless", "workbook never reached disk")
else:
    out = h.do_excel_open({"path": BOOK,
                           "write_res_password": "not-needed-here",
                           "ignore_read_only_recommended": True})
    check("extra open params are harmless on an unprotected workbook",
          "error:" not in out, out)
    check("no spurious read-only warning from the extra params",
          "READ-ONLY" not in out, out)

# ── 9. the quit guard (never quits over other workbooks) ───────────────────
print("\n9. quit_app refuses while other workbooks are open")
h.do_excel_open({"path": BYSTANDER, "create": True})
out = h.do_excel_close({"workbook": "check_main.xlsx", "save": True,
                        "quit_app": True})
check("quit refused while a bystander is open",
      "left running" in out.lower() or "still open" in out.lower(), out[:300])
check("Excel was NOT quit", "error:" not in h.do_excel_open({}), "")
h.do_excel_close({"workbook": "check_bystander.xlsx", "save": False})

# ── 10. tables (2026-11-18) ────────────────────────────────────────────────
print("\n10. excel_table")
out = h.do_excel_open({"path": LAYOUT, "create": True})
check("a workbook for the layout checks", "error:" not in out, out)
book = h._excel_book(LAY)
FIRST = book.sheets[0].name                 # 'Sheet1', whatever this Excel calls it
h.do_excel_sheet({"workbook": LAY, "action": "add", "name": "Panes"})   # and it is now the sheet in view
HERE = {"workbook": LAY, "sheet": FIRST}
h.do_excel_write(dict(HERE, start_cell="B8", values=[
    ["Date", "Category1", "Qty"], ["2026-11-18", "PROPERTY", "3"],
    ["2026-11-19", "CAR", "4"], ["2026-11-20", "FOOD", "5"]]))


def table(action, **params):
    return h.do_excel_table(dict(params, workbook=LAY, action=action))


out = table("list")
check("list: no tables yet", out.startswith("No tables in"), out)
out = table("create", sheet=FIRST, range="B8:D11", name="Check_Docs", style="TableStyleMedium2")
check("create: reported with the range Excel gave it",
      out.startswith("Created table 'Check_Docs'") and f"{FIRST}!B8:D11" in out, out)
check("create: its columns are named by letter and header",
      "B=Date, C=Category1, D=Qty" in out, out)
out = h.do_excel_write(dict(HERE, start_cell="G1",
                            values=[["=ROWS(Check_Docs)", "=SUM(Check_Docs[Qty])"]]))
check("the name is a real table name: structured references resolve (3 rows, sum 12)",
      "1\t3\t12" in out, out)
listed = table("list")
out = table("create", sheet=FIRST, range="J8:K9", name="check_docs")
check("create: a name in use is refused before anything exists",
      "already exists" in out and "Nothing was created" in out, out)
for bad, why in (("Has Space", "space"), ("T2", "cell reference"), ("1st", "start with")):
    out = table("create", sheet=FIRST, range="J8:K9", name=bad)
    check(f"create: the name '{bad}' is refused ({why})",
          "cannot be a table name" in out and why in out, out)
out = table("create", sheet=FIRST, range="J8:K9", name="Check_Two", style="NoSuchStyle")
check("create: an unknown style is named as the cause, in words",
      "NoSuchStyle" in out and "Nothing was created" in out and "-2147" not in out, out)
out = table("create", sheet=FIRST, range="C9:E12", name="Check_Over")
check("create: an overlap is refused in words",
      "error:" in out and "Nothing was created" in out and "-2147" not in out, out)
check("no orphan table is left by any refused create", table("list") == listed, table("list"))
out = table("create", sheet=FIRST, range="J8:K8", name="Check_Empty")
check("create: a header row alone becomes header + one empty row, and says so",
      "J8:K9" in out and "not the J8:K8 asked for" in out, out)

# The name rule, measured again against THIS Excel: every name the rule
# passes must be one the display-name setter takes, and vice versa.
live = next(t for t in book.sheets[FIRST].tables if t.name == "Check_Docs")
disagree = []
for candidate in ("Good_Name2", "_u", "x.y", "XFE1", "A1048577",
                  "Has Space", "FY2025", "R1C1", "RC", "R", "1abc", "Tab-le", "T2"):
    by_rule = ExcelMixin._excel_table_name_problem(candidate) is None
    try:
        live.display_name = candidate
        by_excel = True
    except Exception:
        by_excel = False
    if by_rule != by_excel:
        disagree.append(f"{candidate}: rule {by_rule}, Excel {by_excel}")
live.display_name = "Check_Docs"
check("the name rule and this Excel agree on 13 names", not disagree, "; ".join(disagree))

out = table("update", name="check_docs", range="B8:D12", style="TableStyleLight9",
            banded_rows=False, new_name="Check_Docs2")
check("update: resize, restyle, an option and a rename in one call",
      out.startswith("Updated table 'Check_Docs2'") and "resized to B8:D12" in out
      and "renamed from 'Check_Docs'" in out and "⚠" not in out, out)
check("update: the result shows the table as it now is",
      "style TableStyleLight9" in out and "banded rows" not in out, out)
out = h.do_excel_read(dict(HERE, range="G1:H1", formulas=True))
check("a rename follows through into the formulas that use the table",
      "Check_Docs2" in out, out)
out = table("update", name="Check_Docs2", style="NoSuchStyle", banded_columns=True)
check("update: a refused style does not hide what did work",
      "banded_columns=true" in out and "⚠ Not applied: style NoSuchStyle" in out
      and "-2147" not in out, out)
out = table("update", name="Check_Docs2", range="B9:D12")
check("update: a resize that moves the header is refused in words",
      "⚠ Not applied: range B9:D12" in out and "-2147" not in out, out)
out = table("update", name="Check_Docs2", new_name="Check_Empty", style="TableStyleDark1")
check("update: a name in use stops the whole update",
      "already exists" in out and "Nothing was changed" in out, out)
check("... the style it came with was not applied either",
      "TableStyleLight9" in table("list"), table("list"))
out = table("convert_to_range", name="Check_Empty")
check("convert_to_range: reported", out.startswith("Converted table 'Check_Empty'"), out)
out = table("list")
check("convert_to_range: that table is gone, the other stays",
      "Check_Empty" not in out and "Check_Docs2" in out, out)
out = h.do_excel_read(dict(HERE, range="B8:D9"))
check("a table's cells are ordinary cells to the other tools", "PROPERTY" in out, out)

# ── 11. freeze panes (2026-11-18) ──────────────────────────────────────────
print("\n11. excel_freeze_panes")
# The hardest case throughout: a sheet that is NOT in view, in a workbook
# that is NOT in front — what the agent meets when attached to your Excel.
h.do_excel_open({"path": BYSTANDER})
BY = "check_bystander.xlsx"
sheet = book.sheets[FIRST]


def panes(**params):
    return h.do_excel_freeze_panes(dict(HERE, **params))


def with_window(fn):
    """Reach the sheet's window the way the tool does, and put the view back."""
    mark = h._excel_view_mark(book)
    try:
        return fn(h._excel_pane_window(book, sheet))
    finally:
        h._excel_view_restore(book, mark)


def frozen_part():
    """The frozen pane's own cells — Windows only: COM exposes the panes."""
    return with_window(lambda w: str(w.Panes(1).VisibleRange.Address).replace("$", ""))


def hide_rows(first, last, hidden=True):
    # The cross-platform way to hide rows, and the property the tool reads.
    sheet.range((first, 1), (last, 1)).row_height = 0 if hidden else 15


def in_front():
    return h._excel_app().books.active.name


out = panes()
check("report: a sheet that is not in view", out == f"Panes on [{LAY}]{FIRST}: not frozen.", out)
out = panes(rows=8)
check("freeze: the top 8 rows", out.startswith("Froze the top 8 rows"), out)
check("freeze: and that is what the report says", "top 8 rows kept in view" in panes(), panes())
check("the workbook in front stayed in front", in_front() == BY, in_front())
check("the sheet in view stayed in view", book.sheets.active.name == "Panes",
      book.sheets.active.name)
out = h.do_excel_freeze_panes({"workbook": BY})
check("the workbook in front was not frozen instead", "not frozen" in out, out)
if IS_WIN:
    check("the frozen part is rows 1-8", re.fullmatch(r"A1:[A-Z]+8", frozen_part()), frozen_part())
else:
    skip("the frozen part is rows 1-8", "the panes themselves are reachable through COM only")

panes(rows=0)
with_window(lambda w: h._excel_win_set(w, "scroll_row", 50))
out = panes(rows=8)
check("freeze from a window scrolled to row 50", out.startswith("Froze the top 8 rows"), out)
if IS_WIN:
    check("... freezes rows 1-8, not 50-57", re.fullmatch(r"A1:[A-Z]+8", frozen_part()),
          frozen_part())
else:
    skip("... freezes rows 1-8, not 50-57", "the panes themselves are reachable through COM only")
place = with_window(lambda w: h._excel_pane_state(w)["scroll_row"])
check("... and scrolls back to where the user was", place == 50, place)

hide_rows(9, 20)                    # frozen FIRST, hidden afterwards: the everyday order
out = panes()
check("a freeze made first and hidden-under later reads true, with no warning",
      "top 8 rows kept in view" in out and "⚠" not in out, out)
out = panes(rows=8)
if IS_WIN:
    check("asking for the freeze it already has is not refused", "already frozen that way" in out, out)
else:
    check("asking for the freeze it already has (macOS cannot tell: refused or re-made)",
          "is hidden" in out or out.startswith("Froze"), out)
panes(rows=0)
out = panes(rows=8)
check("a NEW freeze over a hidden row is refused, untouched",
      "row 9, directly below the 8 rows to freeze, is hidden" in out
      and "Nothing was changed" in out, out)
check("... the sheet is still unfrozen", "not frozen" in panes(), panes())
out = panes(rows=5)
check("a freeze that ends on a shown row is fine", out.startswith("Froze the top 5 rows"), out)
panes(rows=0)
# Why the rule exists, measured on THIS Excel: make the freeze behind the
# tool's back and see what Excel does with the hidden rows.
with_window(lambda w: h._excel_pane_apply(w, 8, 0))
out = panes()
check("a freeze made over hidden rows is reported with its swallowed rows",
      "top 8 rows kept in view" in out and "⚠ Hidden rows from row 9" in out, out)
hide_rows(9, 20, hidden=False)
out = panes()
check("Excel does swallow them: shown again, 20 rows are frozen (the reason for the rule)",
      "top 20 rows kept in view" in out, out)
panes(rows=0)

if IS_WIN:
    # The same through a real filter — the shape of a filtered table under a
    # frozen header.
    native = next(t for t in sheet.tables if t.name == "Check_Docs2").api
    native.Range.AutoFilter(Field=3, Criteria1=">100")      # every data row filtered out
    out = panes(rows=8)
    check("filtered: a new freeze of the header rows is refused",
          "row 9, directly below" in out and "filtered out?" in out, out)
    native.AutoFilter.ShowAllData()
    out = panes(rows=8)
    check("filter cleared: the same freeze goes through", out.startswith("Froze the top 8 rows"), out)
    native.Range.AutoFilter(Field=3, Criteria1=">100")
    out = panes()
    check("filtered afterwards: still reads 8 rows, no warning",
          "top 8 rows kept in view" in out and "⚠" not in out, out)
    check("filtered afterwards: asking again is 'already so'",
          "already frozen that way" in panes(rows=8), panes(rows=8))
    native.AutoFilter.ShowAllData()
    check("filter cleared again: exactly rows 1-8 are frozen",
          re.fullmatch(r"A1:[A-Z]+8", frozen_part()), frozen_part())
    panes(rows=0)
else:
    for n in ("filtered: a new freeze of the header rows is refused",
              "filter cleared: the same freeze goes through",
              "filtered afterwards: still reads 8 rows, no warning",
              "filtered afterwards: asking again is 'already so'",
              "filter cleared again: exactly rows 1-8 are frozen"):
        skip(n, "the filter is set through COM here")

out = panes(rows=1, columns=2)
check("rows and columns together", out.startswith("Froze the top row and left 2 columns"), out)
out = panes(rows=1048575)
check("an absurd count is caught and the panes put back",
      "could not freeze" in out and "put back as they were" in out, out)
check("... as they were", "top row and left 2 columns kept in view" in panes(), panes())
out = panes(rows=-1)
check("a bad count is refused by name", "'rows' cannot be negative" in out, out)
out = panes(rows=0, columns=0)
check("unfreeze", out.startswith("Unfroze the panes"), out)
check("unfreeze again: nothing to do", "nothing to do" in panes(rows=0), panes(rows=0))
check("at the end the workbook in front is still in front", in_front() == BY, in_front())
check("... and the sheet in view still in view", book.sheets.active.name == "Panes",
      book.sheets.active.name)
h.do_excel_close({"workbook": LAY, "save": False})
h.do_excel_close({"workbook": BY, "save": False})

# ── summary ────────────────────────────────────────────────────────────────
for p in (BOOK, BYSTANDER, LAYOUT, os.path.join(TMP, "check_readonly.xlsx")):
    try:
        force_rm(p)
    except Exception:
        pass
try:
    os.rmdir(TMP)
except Exception:
    pass

failed = [n for n, ok, _ in RESULTS if ok is False]
skipped = [n for n, ok, _ in RESULTS if ok is None]
passed = [n for n, ok, _ in RESULTS if ok is True]
print("\n" + "=" * 68)
print(f"platform: {'Windows (COM)' if IS_WIN else 'macOS (AppleScript)'}   "
      f"checks: {len(RESULTS)}   passed: {len(passed)}   "
      f"failed: {len(failed)}   skipped: {len(skipped)}")
if failed:
    print("\nFAILED:")
    for n in failed:
        print("  -", n)
print("=" * 68)
if PRIVATE:
    print("Ran in an Excel instance of its own, closed on the way out; the Excel "
          "you have running was never attached to.")
else:
    print("Excel was left running; your own workbooks were never touched.")
sys.exit(1 if failed else 0)
