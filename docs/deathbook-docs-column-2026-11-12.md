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
header row, aborts before any write if a required header is missing or repeated, and works with or
without the `Docs` column. **No helper writes `Docs`**: the user has not yet said what goes in it.

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

## Open

- **Not run against the live workbook.** First live use of each helper: dry-run first
  (`add_docket.py check` prints the live headers and table range without writing).
- **The workbook's own VBA was not inspected** (`SortByDate`, `Select_WBCSAV` / `Select_WBCMC` /
  `Select_ANZSAV` / `Select_ANZBIG`, `CLEARCategory1FILTERS`, `CLEARsourceFILTERS`, `Snapshot`,
  `SortTableALL`). A macro that filters by field number or a fixed range would now hit the wrong column.
- `process-payment-emails/scripts/archive_one_offs/` still uses fixed letters; archived, do not run.
- What `Docs` should hold, and whether the helpers should fill it.
