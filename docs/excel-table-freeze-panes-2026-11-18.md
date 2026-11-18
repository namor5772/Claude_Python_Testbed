# Two Excel layout tools — `excel_table` and `excel_freeze_panes` (2026-11-18)

## The request

That morning a Claude Code session built a `[Documents]` sheet in `DeathBook.xlsm` with a table,
`TableDOCS`, laid out like `Spending!TableALL`. Attaching to the open workbook, reading it back and
saving went through the `excel_*` tools served by `claude_code_mcp/`; the table itself, the frozen
header and the zoom went through a hand-written COM script, because the nine tools covered cells,
formats and sheets and had no operation for a table or a window. Told so, the user asked:

> add table and freeze pane tools to the excel MCP

## The sheet that prompted it — `[Documents]` / `TableDOCS`

Recorded here because nothing else in the repo does: the skills that drive the workbook live in
OneDrive, and `deathbook-workbook`'s sheet list does not name the sheet yet.

- **Where.** A worksheet `Documents`, the tab directly after `Spending`. `Data Sources` is still the
  last tab, which is all the reconciliation scripts rely on — they find their sheets by name and
  append `Data Sources` at the end — and no macro refers to a sheet by position (the workbook's three
  VBA modules, some 1,300 lines, were read for it; `ThisWorkbook` and the sheet modules are empty, so
  nothing fires on a new sheet or a write).
- **The table.** `TableDOCS` at `B8:F9` — header row 8, data from row 9, `TableALL`'s anchor — with
  five columns: Date, Category1, Category2, Category3, Comments, and the one row the user gave.
- **The look**, read from `TableALL` as it stood and matched by header name, never by letter: style
  `TableStyleMedium2` with banded rows and filter buttons (the colours are the style's — `TableALL`'s
  header cells carry no fill of their own, so painting cells would have been the wrong copy); column
  widths 10.18 / 15 / 21.82 / 15.36 for Date and the three categories and 49.73 for Comments, which is
  column J on Spending, behind the same 2.18-wide column A; the Date column `d/mm/yyyy` holding a real
  date; panes frozen below row 8; zoom 85 %.
- **Left out on purpose.** Rows 1-7 are empty: Spending's buttons and status block there are wired to
  `TableALL`'s macros. No tab colour: each of the user's sheets has its own, and it was not ours to
  pick.
- **How.** Attached to the workbook the user had open — no password, no second copy, the same
  workbook they were looking at. The build was rehearsed on a throwaway workbook in a hidden Excel
  instance, then run unchanged against the live one after a `SaveCopyAs` backup,
  `DeathBook_pre_Documents_sheet_20261118_083932.xlsm` in `DeathFinances\Backups` (the helper
  scripts' convention). Read back: 29 checks, among them hashes of every formula and every value on
  Spending taken before and after (identical), every other sheet's used range, the table list and the
  count of defined names; then the saved file's own ZIP, which lists the sheet, the table and the VBA
  part. The workbook saves itself (AutoSave, on OneDrive), so the backup is the way back — or
  deleting the tab.

## What was added

Two tools in `EXCEL_TOOLS` (`myagent/constants.py`), implemented in `myagent/excel_mixin.py`. The
`excel_*` dispatch is the dynamic `do_<name>` lookup in MyAgent, in SelfBot and — through MyAgent's
dispatcher — in the MCP server, so the schema and the method are the whole wiring: MyAgent offers 103
built-in tools (101 before), the MCP server serves 101 (99 before), eleven of them Excel's.

| Tool | What it does |
|---|---|
| `excel_table` | `list` every table (sheet, range, style, each column's letter and header); `create` one over a range whose first row is the headers; `update` (rename, restyle, resize, display options); `convert_to_range` |
| `excel_freeze_panes` | freeze `rows` top rows and / or `columns` left columns; `rows=0, columns=0` unfreezes; with neither, reports the sheet's panes |

`list` exists for the rule the DeathBook skills already follow — address a table's columns by header
name, never by a remembered letter — which until now took a script: it prints `B=Date, C=Category1, …`.

Neither is parallel-safe (one shared COM apartment, like the other nine), and neither asks for a
confirmation: `convert_to_range` keeps the cells, and nothing here deletes data.

## What a real Excel turned out to do

Everything below was measured that day on Excel 16.0 with xlwings 0.36.12, in a hidden Excel
instance of its own, never the user's. The tools are shaped by these answers, and the unit tests'
fake window behaves the same way.

### Tables

1. **xlwings names a table after it has created it.** `Tables.add(name=…)` is `ListObjects.Add`
   followed by a name assignment, so a refused name leaves the table behind under Excel's automatic
   one. Seen with a duplicate name: an orphan `Table4`, and an error that said nothing
   (`0x800A03EC`, no description).
2. **A table has two names, and only one setter checks.** `.Name` — what xlwings' `table.name`
   sets — takes anything and quietly gives the table a different name for formulas. `.DisplayName`
   validates and refuses atomically:

   | Asked for | via `.Name` | via `.DisplayName` |
   |---|---|---|
   | `Has Space` | Name `Has Space`, formulas use `Has_Space` | refused |
   | `A1`, `TAB1`, `FY2025` | formulas use `A1_`, `TAB1_` | refused (cell references) |
   | `R1C1`, `RC`, `R`, `C` | formulas use `_R1C1`, `R_`, `C_` | refused |
   | `1abc`, `Tab-le` | `_1abc`, `Tab_le` | refused |
   | a defined name's (`MyName`) | `MyName_1` | refused |
   | another table's, any case | refused | refused |
   | `_u`, `x.y`, `a?b`, `\b`, `ÉtéTable`, 255 characters | taken | taken |
   | `XFE1`, `A1048577` (past the sheet's edge) | taken | taken |

   Short names are the trap for a model: `T2`, `Q4` and `FY2025` are cell references.
3. **An unknown style is refused before anything is made** when it goes in with the creation, with
   no description; set on an existing table, pywin32 words the refusal "Invalid number of
   parameters" and the table keeps the style it had.
4. **`ListObject.Unlist` keeps the look.** The values stay, the style's colours and bold stay as
   ordinary cell formatting, the name and the filter buttons go — Excel's own Convert to Range.
5. **A range with no data row grows by one**: a header row alone (`J8:K8`) becomes `J8:K9`, a single
   cell a one-column table the same way. Blank and duplicate headers are rewritten by Excel
   (`Column3`, `dup2`), numeric ones become text. xlwings' Windows engine ignores `has_headers` and
   always passes "has headers", so the tool's contract is simply that the first row is the header.

### Panes

1. **xlwings has no freeze panes for desktop Excel.** `Sheet.freeze_panes` is implemented for its
   remote (Office.js) engine alone; the Windows and macOS engines raise `NotImplementedError`.
2. **A workbook's own window can be frozen while another workbook is in front.**
   `book.api.Windows(1)` takes the freeze with the workbook inactive and Excel hidden; the active
   workbook's window was untouched. Panes belong to a (window, sheet) pair, though, and can only be
   read or set for the sheet the window currently shows.
3. **A freeze is taken from the window's scroll position.** Asked for 8 rows while scrolled to row
   50, Excel froze rows 50-57 and put rows 1-49 out of reach. Such a freeze then reads exactly like
   a proper one — same flag, same split; only the frozen pane's own scroll position differs.
4. **`FreezePanes = False` leaves an ordinary split behind.**
5. **A freeze made over hidden rows swallows them.** Excel positions a split in points and a hidden
   row has no height, so with rows 9-20 hidden "below row 8" and "below row 20" are one position,
   and Excel settles on the far end. Hidden, the sheet looks right. Shown again:

   | Made | While hidden | After the rows are shown |
   |---|---|---|
   | 8 rows frozen, THEN rows 9-20 hidden | `SplitRow` 8 | 8 rows frozen |
   | rows 9-20 hidden, THEN 8 rows frozen | `SplitRow` 19 | **20 rows frozen** |
   | 1 header row frozen, THEN a filter applied | `SplitRow` 1 | 1 row frozen |
   | a filter hiding rows 2-31, THEN 1 row frozen | `SplitRow` 30 | **31 rows frozen** |

   Columns behave the same (2 frozen over hidden C:E became 5), and so does Excel's own
   freeze-at-the-active-cell route. A freeze made first and filtered afterwards is stable.
6. **The read-backs are not what was set.** `SplitRow` reads one short while the frozen part ends in
   a hidden run (19 for a split after row 20), and one short when the frozen part is larger than the
   window shows (39 for 40, 499 for 500). An absurd count raises nothing at all: 1,048,575 rows
   became a freeze of 16 rows by 10 columns, the middle of the window.
7. **Page Layout view has no panes**, and a freeze there fails on its last step, after the panes the
   sheet had are already gone.
8. **Showing a sheet of a workbook that is not in front** brought that workbook forward in some
   runs and not in others, and activating a hidden sheet raises nothing and changes nothing.

## What the tools do about it

- **A table's name is settled before the table exists.** `_excel_table_name_problem` applies
  Excel's rules (it agrees with the display-name setter on all 24 names measured, and the live check
  re-measures 13 against whatever Excel it runs on), and `_excel_table_clash` looks for the name
  among the tables and the defined names. The table is then named through `display_name`. A refusal
  costs nothing: "Nothing was created". A name Excel still refuses after creation is reported, with
  the table left under its automatic name for `update` or `convert_to_range` — nothing is guessed at
  or rolled back blind.
- **The freeze goes through the workbook's own window**, never `Application.ActiveWindow`. The agent
  is attached to the user's Excel: through the active window, a freeze lands on whatever is in front
  at that instant. What the window shows is checked, not assumed (the hidden-sheet case).
- **The sequence is fixed**: unfreeze, clear the split, scroll to the top-left corner, set the
  split, freeze — then read back (`_excel_pane_landed`) and, if Excel did something else, put the
  panes back as they were and say so.
- **A new freeze over a hidden row is refused, untouched**, with the reason and the remedy (show the
  rows, then freeze). The alternative is a layout that is wrong only later, when a filter is
  cleared, with nothing to connect it to its cause.
- **Asking for the freeze a sheet already has does nothing** — which is also what keeps the everyday
  sheet, frozen first and filtered since, from being refused by that rule. "Already has" includes
  the frozen pane starting at row 1 (`_excel_pane_home`), so a freeze made while scrolled is made
  again rather than waved through.
- **The report says what is visibly frozen**, and warns when hidden rows lie inside the frozen part
  (`_excel_pane_extent`, by bisection — a filter can hide thousands of rows).
- **The view is put back**: the active sheet, the active workbook, and the user's scroll position —
  the last only where the user had actually scrolled, since a window with two frozen columns reads
  `ScrollColumn` 3 while showing column A, and that 3 put back as a bare number scrolled A and B out
  of view.
- **Errors are worded**: a COM error with no description prints as a tuple of numbers, so
  `_excel_reason` substitutes what is known ("most likely it does not know the style …").

## One more thing in xlwings worth knowing

On Windows, `App.quit()` and `App.kill()` end with `Apps.cleanup()`, and the same function is an
`atexit` hook of every process that imports xlwings: it lists every `EXCEL.EXE`, subtracts those it
can enumerate, and `taskkill /F`s the rest. An instance is enumerable when a top-level window has an
`XLDESK` child with an `EXCEL7` child — a workbook window. Read from the source, not provoked: an
Excel sitting on its start screen with no workbook would be on the kill list. It is how
`excel_close(quit_app=true)` has always ended, and it is unchanged here. What follows from it today:
the private instances the live check and the day's probes start are quit through raw COM
(`app.api.Quit()`), never through xlwings.

## Tests

- **`tests/test_excel_tables_panes.py`** — 86 tests, no Excel, no xlwings. The pure helpers against
  the measured values; both schemas against the one tuple and the one option table they share with
  the mixin; both tools over fakes. The fake window is not a call recorder alone: it freezes from
  the scroll position, leaves a split on unfreeze, swallows hidden runs, reads one short, substitutes
  the mid-window freeze and refuses in Page Layout view, so a wrong order or a missing guard comes
  out as a wrong outcome. Checked the other way round too: fourteen safeguards were disabled one at
  a time (the scroll-to-top step, the split clear, the hidden-row rule, the read-back, the name rule,
  the clash check, naming through `.name`, …) and each was caught by at least one test.
- **`tests/check_excel_live.py`** — sections 10 (tables, 24 checks) and 11 (panes, 30 checks),
  among them the real thing: an AutoFilter under a frozen header. 79 of 79 on Windows. Two changes to
  the check itself, both about the workbook that was open that day:
  - **Every call names its workbook.** Sections 2-6 addressed "the active workbook", which is the
    user's the moment they click on it mid-run.
  - **`--private`** runs the whole check in a hidden Excel instance of its own, by overriding the
    one method every tool attaches through (`_excel_app`), so the running Excel is never attached to.
- **The MCP server**, spawned fresh over stdio as Claude Code spawns it: 101 tools, eleven
  `excel_*`, and both new ones routed (called with input they refuse before Excel is touched).
- The suite: 124 modules, 2,479 tests.

## Open

- **macOS.** The table tool rides on xlwings' cross-platform `tables` API; the two native calls do
  not, and have not been run on a Mac: `convert_to_range` (the AppleScript command of that name, from
  xlwings' terminology table) and the panes, which AppleScript reaches only through the
  application's active window — so there the workbook is brought forward and checked before anything
  is set, and "already frozen" cannot be told, so such a request is made again. Run
  `python tests/check_excel_live.py` on the Mac: the checks that need COM skip themselves and name
  why.
- **What the Documents sheet still needed beyond these two**: the zoom, and placing the new sheet
  right after Spending (`excel_sheet add` appends at the end).
- **A report on a frozen part larger than the window reads one short** (39 for 40). The freeze
  itself is warned about when it is made.
- **The `deathbook-workbook` skill was not edited.** It still sends structural edits to a COM
  script and tells a run to map `TableALL`'s columns by reading header row 8 — `excel_table list`
  now answers that in one call — and its sheet list does not name `Documents`.

## Afterwards: the tools in the session that asked for them

The session's own MCP server had been started before the change and went on serving 99 tools. The
user reconnected it that afternoon (`/mcp`, 15:23): a new server process, 101 tools,
`mcp__myagent__excel_table` and `mcp__myagent__excel_freeze_panes` listed with the schemas above,
and `excel_freeze_panes` answering through it — a refusal that needs no Excel, which by then was
closed. No restart of Claude Code. Every open session has its own server process and reconnects on
its own: two more were running on the laptop at that hour, started before the change by sessions
still open, and they serve the old catalogue until they reconnect as well.

The table-name rule was closed off the same afternoon. Of its 24 names, 22 had been given to the
display-name setter in the morning's probes; the other two (`TableDOCS`, `Table 1`) and a
255-character name through `.Name` were measured then, and came out as the rule says.
