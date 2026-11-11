# DeathBook `Docs` column: the skills and helper scripts made header-driven (2026-11-12)

The user inserted a whole sheet column into the Spending sheet of `DeathBook.xlsm` and named it `Docs`,
between `Column1` and `Details` in `TableALL`. Everything from G rightwards moved one letter:

| Column   | Before | After |
|----------|--------|-------|
| Date … Column1 | B:F | B:F (unchanged) |
| Docs     | —      | G     |
| Details  | G      | H     |
| Amount   | H      | I     |
| Comments | I      | J     |
| Account  | J      | K     |
| Month (formula) | K | L  |
| Table range | `$B$8:$K$n` | `$B$8:$L$n` |
| Empty side columns checked before an insert | L:T | M:U |
| Mirrored helper block / Table7 | Z:AI / X:AG | AA:AJ / Y:AH |
| Status cells | K2:K3, AH3:AH4 | L2:L3, AI3:AI4 |

Everything changed lives in the OneDrive skills tree (`<OneDrive>/MyAppShare/skills/`), outside this repo;
it reaches the other machines through OneDrive, not `git pull`. No file in this repo changed apart from
this write-up and its link.

## Rule from now on

TableALL columns are located by **header name**, never by letter or offset. Each helper reads the live
header row and aborts before any write if a required header is missing or repeated. What `Docs`
holds was settled later the same day: see **Docs is the document code** below.

## What changed

Scripts that hardcoded positions and would have written one column out:

- `docket-processing/scripts/add_docket.py` — new `table_columns()`; `add`, `similar`, `recent` and
  `show` all go through it. `K_FORMULA` was renamed `MONTH_FORMULA`. The side-column emptiness check
  follows the table's last column. The JSON plan gains `columns` (header → worksheet column).
- `process-payment-emails/scripts/add_payment_entry.py` — new `column_index()`;
  `duplicate_candidates()` takes the index map as a seventh argument. JSON keys renamed:
  `spending_row_values_B_to_J` → `spending_row_values` (a dict by header), `spending_month_K` →
  `spending_month`, `spending_month_K_is_formula` → `spending_month_is_formula`,
  `spending_B_serial_is_integer` → `spending_date_serial_is_integer`; `spending_columns` is new.
  The Recurring Payments letters (F/H/L/R, M:T, B:W) are a different sheet and did not change.
- `process-payment-emails/scripts/remove_spending_duplicate.py` — the `B:K`, ten-column layout guard
  is now a header check; the Month column and the outside-the-table checks follow the live bounds.

Already header-driven, wording only: `westpac-recon/scripts/westpac_recon.py`,
`bank-recon/scripts/bank_recon.py` (the provenance note no longer names letters), and
`docket-processing/scripts/ShowDocuments.bas` (unchanged).

Skill text: `deathbook-workbook`, `docket-processing` (+ `references/cash-out-splits.md`),
`process-payment-emails`, `westpac-recon`, `bank-recon`, `email-attachment-processing` — letters replaced
by header names ("B/H key" → "Date/Amount key").

## Verification

Offline only — the workbook is never opened from a Claude Code session.

- `docket-processing/tests`: `test_text_dockets`, `test_receipt_splits`, `test_show_documents_source` —
  78 tests pass. The fakes now model the B:L layout; new tests cover the pre-Docs layout, a missing
  header, and `Docs` staying empty after an add.
- `process-payment-emails/tests/test_duplicate_guard` — 21 tests pass, including both layouts.

Later the same day, at the user's explicit request and with the workbook open, the read-only steps ran
against the live table: `add_docket.py check`, `recent`, `show --no-open`, `add --dry-run` and
`add_payment_entry.py --dry-run` all resolved the B:L layout correctly and wrote nothing.

## Docs is the document code (user rule, 2026-11-12)

A row's document status moved out of the front of `Details` into `Docs`:

| Details used to begin | Docs | Details now |
|---|---|---|
| `ATTACH: ` | `A` | the rest |
| `ATTACH: AI{n} created - ` | `A{n}` | the rest |
| `DOCKET: ` | `D` | the rest |
| `DOCKET: AI{n} created - ` | `D{n}` | the rest |

`{n}` is the Recurring Payments Index. Two shapes the user did not list were left as they are:
`NO DOCKET: ` (a deliberately fileless record; `Docs` empty) and a bare `AI{n} created - ` on a payment
row that has no document at all.

- `add_docket.py` — `format_details()` returns `(docs_code, details)`: a real source file gives `D`, or
  `D{n}` when `--details` starts `AI{n} created - ` (the marker is removed); no file gives an empty
  `Docs` and a `NO DOCKET: ` Details. A document add aborts if the table has no `Docs` column.
  `show` treats `Docs` `D…` as a docket row and still recognises an old Details prefix.
- `add_payment_entry.py` — with `--attach` the row gets `Docs` `A{index}` and unprefixed Details;
  the duplicate guard matches the index as `A{index}` / `D{index}` in `Docs` as well as the older
  `AI{index} created` in Details.
- `ShowDocuments.bas` — the source reads the `Docs` cell (`docsCode`) for the same-date fallback.
  Installed in the workbook the same day with `install_show_documents.py update-module --label
  docs_code`, once the user had enabled VBA-project trust: integrity verified, compile forced, workbook
  saved; 69 fixture checks and live parity over 258 rows passed. The first run stopped on a text
  mismatch (a missing space in the source that the VBA editor normalises); the source was fixed and the
  second run completed. Roll back from `Backups/DeathBook_before_SHOW_DOCUMENTS_docs_code_20261112_065814.xlsm`,
  not the `_065838` copy.
- New: `deathbook-workbook/scripts/migrate_details_prefix_to_docs.py` (dry run by default) and
  `deathbook-workbook/tests/test_migrate_prefix.py`.
- Skill text rewritten to the rule: `docket-processing` (+ both references), `process-payment-emails`,
  `deathbook-workbook`, `email-attachment-processing`.

**Live migration applied** with the workbook open, at the user's request: 243 of 9,982 rows changed
(160 `D`, 17 `D{n}`, 23 `A`, 43 `A{n}`), only their `Docs` and `Details` cells. Every other cell, the row
count, the table range and the Month formulas were verified unchanged before the save. Three rows had
nothing after the prefix and now have an empty Details (9451, 9585, 9958); row 9984 used `AI23 created :`
and became `D23`. Not migrated: 16 bare `AI{n} created` rows and 1 `NO DOCKET:` row. Backup:
`DeathFinances/Backups/DeathBook_pre_docs_prefix_migration_20261112_065234.xlsm`. A read-only audit
afterwards found every coded row still resolving to its document (exact, attachment or shared receipt),
except row 9839, which relies on the same-date fallback.

Offline suites after this change: docket 84 tests, payment 23, migration rule 6 — all pass.

## LAYOUT button (same day)

At the user's request a **LAYOUT** button was added to the Spending sheet, right of SHOW DOCUMENTS
(`btnLayout`, macro `LayoutToggle`, new standard module `modLayout`). It toggles the sheet between two
sets of column widths and changes nothing else. Each click first overwrites the stored widths of the
layout that is showing with the sheet's current widths, then applies the other layout; there is no
separate save step. (A fixed-layout variant, saving only through a `LayoutSave` macro, was installed in
between and replaced at the user's request the same day.) `LayoutSave` (Alt+F8) remains for storing
the current widths without switching; `LayoutSaveActive` is its dialog-free equivalent. Both layouts
currently hold today's widths, so a click moves nothing until layout 2 has been adjusted. The caption
shows the active layout.

Later the same day the button was extended from two layouts to **three**: each click cycles
1 → 2 → 3 → 1 (`LAYOUT_COUNT` in `Layout.bas`, `COUNT` in the installer), with a fourth hidden name
`LAYOUT_Widths3`. All three layouts hold today's widths until their columns are dragged. Widths live in three hidden workbook names (`LAYOUT_Current`,
`LAYOUT_Widths1`, `LAYOUT_Widths2`).

Source and guarded installer: `deathbook-workbook/scripts/Layout.bas` and `install_layout_button.py`
(`install` / `update-module` / `status`). The install verified every sheet's cells, the other two VBA
modules, the other shapes and the column widths unchanged, forced a compile, toggled there and back
with the widths restored exactly, and saved. Backup:
`Backups/DeathBook_before_LAYOUT_install_20261112_081919.xlsm`.

Two things learned about scripted VBA installs, now noted in the skill: the VBA editor keeps one
spelling per identifier across the whole project (`modShowDocuments` declares `count`, `name`, `names`,
`text`, so new source must use those spellings), and it reformats lines (`x =y` becomes `x = y`), so a
source that is compared with the live text must be written exactly as the editor formats it.

## Blank Docs cells hold a lone apostrophe (same day)

So that Column1's text cannot spill over an empty `Docs` cell, every blank `Docs` cell in TableALL now
holds a lone apostrophe: an empty text that shows nothing and reads back as `""`. 9,739 cells were
filled in one verified write (the 243 coded cells and every other column unchanged; backup
`Backups/DeathBook_pre_fill_blank_docs_20261112_084821.xlsm`). `add_docket.py` and
`add_payment_entry.py` write the apostrophe into `Docs` for a new row that has no code, and
`deathbook-workbook/scripts/fill_blank_docs.py` (dry run by default) fills any cells still truly
empty, for example after rows typed by hand. All readers already treated `""` like empty.

## DATE_STAMP moved to C1 (same day)

The sheet-level name `Spending!DATE_STAMP` (a timestamp cell, yellow, `dd/mm/yyyy hh:mm:ss`) was moved
from J3 to C1 with a cut, which carries the value and formatting and re-points the name. Nothing else
needed adjusting: no formula on any sheet, no line of the workbook's VBA, no skill and no saved
instruction refers to the name or to J3. Every other cell, name and column width was verified
unchanged. The `deathbook-workbook` skill now says to address the cell by name. Backup:
`Backups/DeathBook_pre_move_DATE_STAMP_20261112_090009.xlsm`. Afterwards, at the user's request, C1 was
merged with the empty D1 and its format changed to `dd-mmm-yy hh:mm`; the name still refers to the
single cell C1, so reading it by name returns the same value as before.

Later still it was moved one cell left: `DATE_STAMP` is now **B1, merged B1:C1**, format
`dd-mmm-yy    hh:mm`, and D1 is a plain empty cell again (backup
`Backups/DeathBook_pre_move_DATE_STAMP_to_B1_20261112_093024.xlsm`). No formula, macro or skill refers
to those cells. **One saved MyAgent instruction does: `SchedulePayment` ends with "In Cell B1 of the
[Spending] tab place the text `AI - {timestamp}`", which would now overwrite the stamp.** At the
user's choice that step now reads "set the named cell DATE_STAMP … to the current date and time as a
real date-time value (not text), keeping its existing formatting. Write nothing else into B1:C1."
It was a one-sentence byte edit of `<OneDrive>/MyAppShare/agent_instructions.json` (only that entry's
`text` changed; backup `agent_instructions.pre_DATE_STAMP_20261112.bak` beside it). No MyAgent was
running; an app that loaded the store earlier must be restarted before it saves instructions.

## CLEAR Account FILTERS fixed (same day)

`CLEARsourceFILTERS` in the workbook's `Module1` still cleared filter field 9, which was Account before
the `Docs` column and is Comments now, so the button no longer cleared an Account filter. The line now
finds the field by name (`ListColumns("Account").Index`). Tested through the macro: an Account filter
is cleared, a Comments filter is left alone; table values, the other modules and the selection were
unchanged. Backup: `Backups/DeathBook_pre_fix_CLEARsourceFILTERS_20261112_090420.xlsm`.
`CLEARCategory1FILTERS` had the same fault: it cleared fields 2 to 9 by number, which used to run from
Category1 to Account and stopped at Comments after the insert. At the user's word it now loops from
`ListColumns("Category1")` to `ListColumns("Account")` by name. Tested through the macro with filters on
Category1, Docs, Comments, Account and Month: the first four were cleared and Month was left alone.
Backup: `Backups/DeathBook_pre_fix_CLEARCategory1FILTERS_20261112_091009.xlsm`.

## Open

- **The first real insert under the new rule has not happened yet.** The write paths are covered by the
  offline suites and the dry runs only.
- The Show Documents module has not been compiled on the Mac since this change.
- The user should untick *Trust access to the VBA project object model* again if it is still on.
- The workbook's own VBA (`SortByDate`, the `Select_*` buttons, the CLEAR filter macros, `Snapshot`,
  `SortTableALL`) was not inspected from here; the user updated and checked those macros by hand
  (reported 2026-11-12), so this item is closed.
- `process-payment-emails/scripts/archive_one_offs/` still uses fixed letters; archived, do not run.
- Whether `NO DOCKET: ` rows and bare `AI{n} created - ` rows should also get a `Docs` code.
