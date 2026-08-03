"""Live-Excel check for the excel_* tools. Run it by hand, on either OS.

    python tests/check_excel_live.py

NOT part of `python -m unittest discover` (the filename doesn't match test*.py)
because it needs a running Excel. The pure helpers are unit-tested in
tests/test_excel_mixin.py; this exercises the parts that only a real Excel can
answer, and asserts the places where Windows COM and macOS AppleScript are
expected to DIFFER.

Safety: every workbook it touches lives in a fresh temp directory, and every
close names its workbook explicitly — it never closes "the active workbook",
which could be one of yours. It never quits Excel. Workbooks you already have
open are only ever read as bystander names.
"""

import os
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from myagent.excel_mixin import ExcelMixin  # noqa: E402

IS_WIN = sys.platform == "win32"
RESULTS = []


class Host(ExcelMixin):
    pass


h = Host()
# NOT the system temp dir: on macOS that is /var/folders/..., which Excel's
# sandbox refuses to save into (OSERROR -50, and it looks like a path bug).
# A folder in the user's home works on both OSes. Override with argv[1].
TMP = (sys.argv[1] if len(sys.argv) > 1
       else os.path.join(os.path.expanduser("~"), "myagent_excel_check"))
os.makedirs(TMP, exist_ok=True)
BOOK = os.path.join(TMP, "check_main.xlsx")
BYSTANDER = os.path.join(TMP, "check_bystander.xlsx")


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
print(f"     (your Excel before we start: {pre.splitlines()[0]})")
for p in (BOOK, BYSTANDER):
    force_rm(p)
out = h.do_excel_open({"path": BOOK, "create": True})
check("create + save a new workbook", "error:" not in out, out)
check("a fresh writable workbook gets NO read-only warning",
      "READ-ONLY" not in out, out)

# ── 2. small write: echo + no false drop warning ────────────────────────────
print("\n2. small write (<=200 cells: echoes values back)")
out = h.do_excel_write({"start_cell": "A1", "values": [
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
out = h.do_excel_write({"start_cell": "A10", "values": big})
check("large write reports success", "error:" not in out, out[:200])
check("large write is NOT echoed (would swamp the model)",
      "Current values" not in out, out[:200])
check("no false VERIFICATION FAILED on a landed large write",
      "VERIFICATION FAILED" not in out, out[:300])
back = h.do_excel_read({"range": "A10:C12"})
check("large write really landed", "r0c0" in back, back[:200])

# ── 4. formulas + the currency/Decimal wire-type regression ─────────────────
print("\n4. formulas and the currency wire type")
out = h.do_excel_read({"range": "E1:E4", "formulas": True})
check("formulas read back as formulas", "=SUM(" in out, out[:200])
h.do_excel_format({"range": "C2:C3", "number_format": "$#,##0.00"})
out = h.do_excel_read({"range": "C2:C3"})
# On Windows COM these cells come back as decimal.Decimal, NOT float. This is
# the regression _excel_cell_str exists for; it is a no-op on macOS.
check("currency cells display cleanly (Decimal path)",
      "19.95" in out and "19.9500" not in out, out[:200])
out = h.do_excel_find({"text": "19.95"})
check("numeric find matches a currency-formatted cell",
      "C3" in out, out[:200])

# ── 5. sheet resolution (rewritten 2026-08-01) ──────────────────────────────
print("\n5. sheet resolution")
h.do_excel_sheet({"action": "add", "name": "Data Sheet"})
out = h.do_excel_sheet({"action": "activate", "name": "NoSuchSheet"})
check("missing sheet -> friendly message, not a raw COM/OSERROR",
      "not found" in out and "Sheets:" in out, out[:200])
check("missing sheet error does not leak a driver error",
      "OSERROR" not in out and "0x8" not in out, out[:200])
out = h.do_excel_write({"sheet": "data sheet", "start_cell": "A1",
                        "values": [["case-insensitive"]]})
check("case-insensitive sheet lookup", "error:" not in out, out[:200])
out = h.do_excel_sheet({"action": "frobnicate", "name": "Data Sheet"})
check("unknown action is named as such (not 'sheet not found')",
      "unknown action" in out, out[:200])
h.do_excel_sheet({"action": "delete", "name": "Data Sheet"})

# ── 6. run_macro: the platforms are EXPECTED to differ ──────────────────────
print("\n6. run_macro on a macro-free workbook")
out = h.do_excel_run_macro({"macro": "NoSuchMacro"})
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

# ── summary ────────────────────────────────────────────────────────────────
for p in (BOOK, BYSTANDER, os.path.join(TMP, "check_readonly.xlsx")):
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
print("Excel was left running; your own workbooks were never touched.")
sys.exit(1 if failed else 0)
