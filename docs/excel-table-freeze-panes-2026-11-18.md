# Excel's layout tools — tables, frozen panes, a place for a sheet, zoom (2026-11-18)

Three requests on one day. The morning's `excel_table` and `excel_freeze_panes` fill this page down
to **Afterwards**; `excel_zoom` and a place for a sheet (`excel_sheet`'s `before` / `after` and its
`move` action) followed that afternoon, **Later the same day**; and that evening the
`deathbook-workbook` skill was brought up to date with all four, **That evening: the skill**.

## The request

That morning a Claude Code session built a `[Documents]` sheet in `DeathBook.xlsm` with a table,
`TableDOCS`, laid out like `Spending!TableALL`. Attaching to the open workbook, reading it back and
saving went through the `excel_*` tools served by `claude_code_mcp/`; the table itself, the frozen
header and the zoom went through a hand-written COM script, because the nine tools covered cells,
formats and sheets and had no operation for a table or a window. Told so, the user asked:

> add table and freeze pane tools to the excel MCP

## The sheet that prompted it — `[Documents]` / `TableDOCS`

Recorded here because nothing else in the repo does: the skills that drive the workbook live in
OneDrive, and `deathbook-workbook`'s sheet list did not name the sheet until that evening.

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
  right after Spending (`excel_sheet add` appended at the end). Both were added that afternoon —
  **Later the same day**, below.
- **A report on a frozen part larger than the window reads one short** (39 for 40). The freeze
  itself is warned about when it is made.
- **The `deathbook-workbook` skill was not edited** at this point. It still sent structural edits
  to a COM script and told a run to map `TableALL`'s columns by reading header row 8 —
  `excel_table list` now answers that in one call — and its sheet list did not name `Documents`.
  It was updated that evening: **That evening: the skill**, at the end.

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

## Later the same day: zoom, and a place for a sheet

### The request

> add zoom and sheet position tools to the excel MCP

The two things the Documents sheet had still needed a script for (**Open**, above).

### What was added

| | What it does |
|---|---|
| `excel_zoom` (a new tool) | set a sheet's zoom, 10 to 400 percent; without `percent`, report it |
| `excel_sheet` `add` with `before` / `after` | the new sheet goes directly before / after the sheet named; without either it goes last, as it always did |
| `excel_sheet` `move` (a new action) | put an existing sheet directly before / after another |

A sheet's place went into `excel_sheet` rather than into a tool of its own: that tool already adds,
renames and deletes sheets, and its actions are one tuple (`EXCEL_SHEET_ACTIONS`) behind both the
schema and the mixin's guard. MyAgent now offers 104 built-in tools, twelve of them Excel's; the MCP
server serves 102. Neither is parallel-safe and neither asks for a confirmation — nothing here
deletes anything.

### What a real Excel turned out to do

Measured as the morning's were: Excel 16.0, xlwings 0.36.12, hidden Excel instances of their own,
never the user's.

#### A sheet's name

1. **xlwings names a sheet after it has made it**, as it does a table. `sheets.add("a/b", after=…)`
   got as far as Excel's "You typed an invalid name for a sheet or chart" and left a `Sheet5` in the
   workbook. A name already in use is the one thing it checks first.
2. **The rule**, from giving each name to an existing sheet — through COM's own `Name` where
   xlwings' public setter screens a name itself (the length, six of the characters, the empty
   name). A refused name leaves the sheet named as it was.

   | Taken | Refused |
   |---|---|
   | 31 characters | 32 characters |
   | `a'b` — an apostrophe inside | `'a`, `a'` — an apostrophe first or last |
   | `Sheet 1`, `x.y`, `déjà`, `a,b`, `a!b`, `a=b`, `#x` | `a/b`, `a\b`, `a?b`, `a*b`, `a[b`, `a]b`, `a:b` |
   | `1`, `A1` — a number or a cell reference is fine here | `History`, `history` — "History is a reserved name." |
   | a single space; a tab or a line break inside | the empty name; a name in use, in any case |

#### Moving a sheet

1. **xlwings has nothing that moves an existing sheet.** `sheets.add(before=, after=)` places a new
   one; `Worksheet.Move` is the native call.
2. **A Move with no valid target does not fail — the sheet leaves the workbook.** `Move()` alone
   made a new workbook holding the sheet. So did leaving Before empty to pass the target as After
   by position, on every one of 16 such moves (the workbook in front and behind, early- and
   late-bound COM). After by name did it once, in a late-bound call of the first probe that the
   later runs did not reproduce; xlwings' own source steers round it under the comment *Hack, since
   "After" is broken in certain environments*.
3. **In a workbook that is not the active one, a Move lands one tab off** — not always, and by a
   rule. Every active sheet, moved sheet and place among four sheets (96 moves, each from a fresh
   workbook), and the same over five sheets for three of the active sheets (120):

   | | The workbook in front | The workbook behind another |
   |---|---|---|
   | `Move(Before=…)` / `Move(After=…)`, four sheets | 96 of 96 right | 68 of 96 |
   | the tool's own form (below), four sheets | 96 of 96 right | 74 of 96 |
   | five sheets | 120 of 120 right (the tool's form) | 80 of 120 (`Before=` / `After=`) |

   All 312 moves made behind — the 222 right and the 90 wrong — landed where one rule puts them:
   **Excel allows for the removal of the workbook's ACTIVE sheet instead of the moved one.** Taking
   a sheet out from the left of the destination shifts the destination down by one; behind, Excel
   asks that of the sheet in view. So the moved sheet lands a tab early or late whenever it and the
   sheet in view lie on opposite sides of the destination. With sheets A B C D:

   | Showing | The move | Asked for | Got |
   |---|---|---|---|
   | A | D before C | A B D C | A D B C |
   | D | A before C | B A C D | B C A D |
   | B | A before B — where A already is | A B C D | B A C D |

   The last row is the one to remember: behind, a move that should change nothing changes
   something. A first, smaller matrix (8 moves) gave the same answers hidden and on screen, early-
   and late-bound, and the live check re-measures two of these moves on whatever Excel it runs on.
4. **A Move shows the moved sheet and brings its workbook forward** — in every one of the 510 moves
   that changed the order.
5. **A protected structure answers a Move with an error that has no text** (a tuple of numbers
   ending in `-2147352565`), and an add with "Add method of Sheets class failed".
6. **A chart sheet is a tab that is not a worksheet.** `Worksheet.Index` counts it; xlwings'
   `book.sheets` does not list it (it is COM's `Worksheets`).
7. **xlwings' own "add after" is thrown by a chart sheet ahead.** On Windows it works the place out
   from `Worksheet.Index` — every tab — over that collection of worksheets only, and for the last
   place it moves a sheet. With one chart sheet ahead of A B C D:

   | `sheets.add("RAW", after=…)` | Left |
   |---|---|
   | A | Chart1 A B **RAW** C D — a tab late |
   | B | Chart1 A B C **RAW** D — a tab late |
   | C | Chart1 A B **RAW** D C — before C's old place, **and C and D swapped** |
   | D | Chart1 A B C D **RAW** — right |

   With two chart sheets ahead, after B left `Chart1 Chart2 A RAW B D C`. The third row is the
   costly one: a sheet nobody named has moved. `add(before=…)` is a plain `Sheets.Add(Before=…)`
   and has none of this. On macOS xlwings adds by reference, with no arithmetic — read from its
   source, not run.
8. Its last-place route also ends in an `Activate` that brings the workbook forward; its other
   routes leave the workbook in front alone.

#### Zoom

1. **`Window.Zoom` belongs to a (window, sheet) pair**, as panes do, and reads as a float (`100.0`).
   Set to 85 on one sheet through the workbook's own window — `book.api.Windows(1)`, with another
   workbook active — the next sheet and the other workbook still read 100.
2. **10 and 400 are taken; 9, 401, 0, −5 and 1000 are refused**, each with the same bare "Unable to
   set the Zoom property of the Window class".
3. **A fraction is dropped without a word**: 85.4, 85.5 and 85.6 all became 85.
4. **Frozen panes are untouched** by a zoom.
5. **Page Layout view keeps a zoom of its own**: 90 set there left Normal view at 120.

### What the tools do about it

- **A new sheet's name is settled before the sheet exists.** `_excel_sheet_name_problem` applies the
  rule above — it agrees with Excel on all 25 names measured, and the live check re-measures 16 on
  whatever Excel it runs on — and the workbook's own sheet names are checked, in any case. A refusal
  costs nothing: "Nothing was added".
- **One form of Move, and only one**: a real sheet as the first, positional argument. "After R" is
  "before R's follower" — looked up among ALL the workbook's tabs, since the follower can be a chart
  sheet — and after the last tab it is two moves, the sheet before R and then R before the sheet.
  `After` is never passed, by name or by position.
- **The workbook is brought forward before a move**, which is where 312 of 312 were right, **and no
  move is made that is not needed**: a sheet already in place is answered "nothing to do".
- **xlwings' "add after" is not used behind a chart sheet** (`_excel_sheet_index_off`: the
  neighbour's `Index` is not its place among the worksheets). There the sheet is added BEFORE the
  neighbour — the plain route — and moved after it.
- **The order is read back whole**, not just the pair: the sheet beside the one named AND every
  other sheet where it was (`_excel_sheet_order`). A miss is tried once more, then reported with
  the order as it stands. A sheet that is no longer in the workbook is said in so many words, and so
  is an order in which other sheets have moved.
- **The view is put back**: the sheet in view and the workbook in front, with the helpers the panes
  use. A new sheet is left in view in ITS workbook, as Excel leaves it; which workbook is in front
  does not change.
- **A protected structure is asked about first**, and refused in words.
- **A zoom is validated before Excel is touched** — a whole number from 10 to 400, a fraction
  refused rather than dropped — set through the workbook's own window (the panes' route: the sheet
  shown and checked, never `ActiveWindow`), and read back. Asking for the zoom a sheet has does
  nothing; Page Layout view's separate zoom is said in the result.

### Two things that were wrong before the commit

Both sat in the working tree with every unit test and every live check passing.

- **The rule for a move made behind.** The first matrix made 8 moves, always with sheet A showing,
  and read as "the sheet lands before the active sheet". One result from an earlier probe, with D
  showing, did not fit, and the 312-move run settled it: that reading fits 215 of the 312, the rule
  above all of them. A matrix that holds one thing constant cannot tell two rules apart. The tool's
  logic never depended on which rule it was; the tests' fake did, and it now moves as Excel was
  measured to.
- **An add that swapped two other sheets and reported success.** The read-back checked that the new
  sheet sat beside the one named. With a chart sheet ahead, xlwings' "add after C" left the new
  sheet before C's old place with C and D swapped; the correcting move then brought the new sheet
  beside C, and the tool answered "Added sheet … after 'C'" over a workbook whose other sheets had
  changed places. Found by putting a chart sheet AHEAD of the neighbour in a probe — the live check
  had only ever put one behind it. Fixed twice over: that route is no longer taken, and the
  read-back is the whole order.

### Tests

- **`tests/test_excel_sheet_zoom.py`** — 62 tests over the fakes of
  `tests/test_excel_tables_panes.py`, which gained a `Worksheet.Move` and an xlwings `sheets.add`
  that BEHAVE as measured: a Move with no real sheet first sends the sheet out; behind, it allows
  for the sheet in view; "add after" does xlwings' own arithmetic, chart sheets and all.
  `MeasuredMoveTests` holds the fake to what Excel did with all 48 `Move(Before=…)` of four sheets,
  behind (fourteen of them a tab off) and in front, and the tool to all 96 requests from a workbook
  that starts behind; the measured outcomes of "add after" behind chart sheets are a table of their
  own, and the tool is held to every place behind one chart sheet and behind two. Checked the other
  way round: eighteen safeguards disabled one at a time were each caught, and so was each fake
  given the wrong behaviour (the first, narrower rule; a Move that is always right; an add that is
  always right).
- **`tests/check_excel_live.py`** — sections 12 (a place for a sheet, 36 checks) and 13 (zoom, 14):
  129 of 129 on Windows, hidden and — with `--show`, new that afternoon — with the private
  instance's windows drawn, which is what an attached Excel's are. Three things are re-measured on
  whatever Excel it runs on before the tool is asked to do better: a move made behind landing a tab
  off (twice — the second tells the rule from the first reading of it), and xlwings' own add thrown
  by a chart sheet ahead.
- **The MCP server**, spawned fresh: 102 tools, twelve `excel_*`, `excel_zoom` and the sheet tool's
  new surface routed (called with input they refuse before Excel is touched).
- The suite: 125 modules, 2,541 tests.

### Open

- **macOS**, again. The move is AppleScript's `move … to before / after`, from xlwings' terminology
  table, and the zoom goes through the application's active window as the panes do there. Neither
  has been run on a Mac.
- **A session's own MCP server** lists `excel_zoom`, and `move` among `excel_sheet`'s actions, after
  its next reconnect (`/mcp`): the one these were written from was still serving 101 tools.
- **The `deathbook-workbook` skill** was still unedited at this commit. With these tools the
  Documents sheet of that morning needs no script: `excel_sheet add … after`, `excel_table create`,
  `excel_freeze_panes`, `excel_zoom` — column widths and number formats were `excel_format`'s
  already. It was updated that evening — next.

## That evening: the skill

> update the deathbook-workbook skill with the new tools

The skill lives in OneDrive (`<OneDrive>/MyAppShare/skills/deathbook-workbook/SKILL.md`), outside
this repo, so what changed is recorded here.

- **The sheet list names `Documents`**, the tab after Spending, and a bullet gives its table:
  `TableDOCS`, header row 8, `B:F` = Date, Category1-3, Comments, frozen below row 8, zoom 85 %,
  rows 1-7 empty by design, nothing in the workbook or the helper scripts reading it, and no
  business rule for it in any skill — a run writes to it only when the task says so.
- **Column maps come from `excel_table list`.** The rule "address TableALL's columns by header
  name, never by a remembered letter" now names the one call that returns each table's live range
  and every column as LETTER=header.
- **A `Layout tools` subsection** under "Efficient reads and structural edits": the four tools with
  their call shapes, and the workbook rules that bound them here. Never rename, resize, restyle or
  convert `TableALL`, `Table2` or `TableDOCS` unless the user asks for exactly that — macros,
  formulas and the helper scripts find them by name. Always give `before=` / `after=` when adding
  a sheet: a bare `add` puts it last, and Data Sources must stay the last tab. Clear a filter
  before a new freeze. One "verified on" line: Windows, that day; none of it run on macOS.
- **The scripted freeze is gone.** The recipe the skill carried —
  `sheet.Activate(); app.ActiveWindow.FreezePanes=False; SplitRow=n; FreezePanes=True` — is the one
  the morning's measurements condemn: the active window, at whatever scroll position it is in. In
  its place: the tool; and, for a MyAgent not yet updated from the repo, the workbook's own window
  and the measured sequence.
- The description names the layout tools (about 440 characters, inside the 600 the conventions
  ask). The headings other skills cite — **Required Excel application shutdown** above all — and
  the bundled scripts are untouched.

How it was put in. A SKILL.md changed from outside is written back by any app that loaded the tree
earlier (`claude_code_mcp/README.md`, **Editing a SKILL.md from a Claude Code session**), so:

1. No MyAgent or SelfBot was running. All 25 skill files were already in the form the serializer
   writes, and none was newer than the session's MCP server.
2. The file was copied to
   `<OneDrive>/MyAppShare/skills_backups/20261118_excel_layout_tools/deathbook-workbook/`.
3. The edit was made with exact replacements, the file's CRLF line endings kept.
4. The full new body went through `manage_skills` `update`. Before sending it, the file was scanned
   for what a retyping loses — non-ASCII characters (six kinds, 70 in all), tabs, trailing blanks,
   runs of spaces (one, inside a number format) — and it arrived exact the first time: the file's
   SHA-256 and modification time were unchanged afterwards, again after a second update that
   changes nothing, and no other file in the tree had been touched.

Not covered: two other Claude Code sessions were open on the laptop with servers of their own,
started before the edit. They hold the old text until they reconnect, and would write it back if
they saved a skill first. The same goes for a MyAgent left running on another machine.
