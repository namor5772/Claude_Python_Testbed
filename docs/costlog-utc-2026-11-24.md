# The cost log's clock is UTC — why, the conversion, and what it does not fix (2026-11-24)

## The report

"I have trouble reconciling my API Cost log spends with the providers websites. I think it is
because cost time is booked in UTC while my costs/files are stamped in local time. Could you fix
that and do any backdating if necessary."

## The short answer

The diagnosis was right. Both apps stamped a cost-log line with `datetime.now()` — the machine's own
clock, Sydney time on all three machines — and both Cost Log viewers bucket a run by the first ten
characters of that stamp. Every provider books by the UTC day. Sydney is ten hours ahead of UTC in
winter and eleven in summer, so a run that ended before 10:00 (11:00 in summer) was on the
provider's **previous** day, and the two day columns disagreed on most days.

Since this day a line's stamp is UTC and says so with a trailing `Z`:

```
before   2026-11-24 10:56:26;Anthropic;claude-opus-5-5;5.2521;mode=Medium;727;Reconcile_Banking_Transactions;…
after    2026-11-23 23:56:26Z;Anthropic;claude-opus-5-5;5.2521;mode=Medium;727;Reconcile_Banking_Transactions;…
```

The viewers read every date from the UTC clock, and the lines already in the logs are converted
where they stand — each log by the machine that wrote it.

## The evidence

**1. The providers' clock.**

- Anthropic: the usage and cost reports split "by UTC bucket" (`bucket_width` of `1m` / `1h` / `1d`),
  and a spend cap's refusals last "until 00:00 UTC on the first day of the next month" (the Claude
  platform docs' *Usage and Cost Analytics API* and *Linked billing* pages, read this day). The
  Console's cost export dates its rows in a column named `usage_date_utc` (the column the
  account's own cost-report skill reads).
- OpenAI: its help centre says the usage dashboard shows its data in UTC, that exports cover full
  UTC days, and that application logs should be converted to UTC before they are compared with
  the dashboard (the *API usage dashboard* articles, found by search this day).

**2. The machines' clock.** A file's modification time is kept in UTC by the filesystem, and OneDrive
carries it across. On the morning of the change each machine's log had been modified exactly eleven
hours before its last line's stamp:

| Machine | Last line's stamp | The file's modification time (UTC) |
|---|---|---|
| laptop | 2026-11-24 10:56:26 | 2026-11-23 23:56:26 |
| desktop | 2026-11-24 10:15:58 | 2026-11-23 23:15:58 |
| Mac mini | 2026-11-24 10:12:41 | 2026-11-23 23:12:41 |

So all three were stamping in Sydney daylight time, and one zone's rules convert all three logs.

**3. What it did to the day rows.** Over the three machines' 2,136 lines, read both ways:

- 682 runs — 32 % of the runs, 38 % of the spend — fall on a different calendar day in UTC than
  they did in local time: the runs that ended before 10:00 or 11:00 in the morning.
- The Anthropic day totals differ on 113 of the 126 days that have one; the OpenAI ones on 86 of 99.
- 18 runs change month, moving a month's total by one to three dollars.

A week of Anthropic rows shows the shape — a large morning run moves a whole day back:

| Day | Stamped in local time | In UTC |
|---|---|---|
| 2026-11-19 | $0.06 | $13.73 |
| 2026-11-20 | $13.73 | $0.04 |
| 2026-11-23 | $0.10 | $5.40 |
| 2026-11-24 (to 11:00 local) | $5.40 | $0.00 |

The right-hand column is the one on the provider's clock.

## The fix

**One place makes a stamp.** `myagent/datapaths.py`, `costlog_stamp(local_stamp=None, tz=None)`:

- no argument — now, in UTC: `2026-11-23 23:56:26Z`;
- a stamp already marked — returned as it is;
- an unmarked stamp — local time, converted by this machine's zone rules for that date (daylight
  saving included; `tz` is the tests' hook);
- anything that is not a stamp — returned unchanged, never raising.

`StreamingMixin._log_api_cost` calls it for the line and `_run_progress_write` for the in-progress
record's `written`; SelfBot's byte-identical copies call the same function. Because `_log_api_cost`
passes its `timestamp` argument through it, an in-progress record left behind by a copy of the app
from before the change (local time, unmarked) is folded in as UTC.

**The mark is on every line, on purpose.** Three machines share the folder, and two of them run the
old code until their next `git pull`. A switch recorded once per file could not tell a line an old
copy appended afterwards from a converted one. With the `Z`, a line says which clock it is on: the
conversion can be repeated safely at every launch, and the machines change over in any order.

The form keeps the first nineteen characters exactly as they were (`YYYY-MM-DD HH:MM:SS`, a space,
not a `T`), so marked and unmarked stamps still sort together as text and an older viewer still
reads the date out of them.

**The conversion** is `migrate_costlog_to_utc(path)`, run by `resolve_costlog()` — the function
every app already calls at start-up to find the log:

- every line whose stamp runs straight into the `;` gets the stamp converted and marked; a bytes
  regex does it, so nothing else in the file changes, the CRLF endings of the oldest Windows lines
  included;
- the file as it stood before its first conversion is kept beside it as
  `APICostLog_<machine>.txt.local-time.bak` (written once, never overwritten; a name neither viewer
  reads);
- the new content replaces the old atomically, from a temp file in the same folder;
- the replace is abandoned if the log changed between the read and the replace — a run ending in
  another process must not lose its line under the converted copy — and the next launch retries;
- without a backup there is no rewrite; any I/O failure leaves the log exactly as it was.

After the first pass the scan finds nothing; on the laptop's 150 KB log it takes about a
millisecond.

**Each machine converts only its own log.** The per-machine files exist so that no two machines
ever write the same synced file: a second writer is how OneDrive makes a conflict copy, and a copy
named `APICostLog_<machine>-<Computer>.txt` would be read by both viewers as one more machine,
doubling that machine's history. The conversion keeps the rule. It also means each log is converted
with the zone rules of the machine that wrote it.

**The viewers** (`CostLog_Win.ps1`, `view_costlog.command`):

- today, the seven day rows and this month come from the UTC clock;
- the `Z` is dropped at ingest, and the FULL LOG's first heading reads `DATE/TIME (UTC)`;
- the SUMMARY opens with the clock and where a UTC day's edges fall on the reading machine's:
  `Dates and times are UTC, the clock the providers bill by: a UTC day runs 11:00 to 11:00 on this
  machine's clock (UTC+11:00).`
- a line without the `Z` — its machine has not started an updated app yet — is converted for
  display by the reading machine's zone, and a note under the clock line counts those runs and
  names their machines. So the report is right on any machine that has pulled, whatever state the
  other machines' logs are in.

## What was done on the day

- **The laptop's log** was converted through `resolve_costlog`: 1,279 lines, 729 by +10:00 and 550
  by +11:00 (the first of those on 2026-10-04, the day daylight saving began), the cost total
  unchanged to the cent, the 216 CRLF endings kept, no line anywhere near the hour the clocks
  skipped. The result is byte-identical to a conversion of a copy checked line by line beforehand.
- **Checked against a clock the log does not hold**: 623 lines of the three logs name a chat file
  this laptop has, and a chat file's modification time is true UTC. It sits within ten seconds of
  the converted stamp on 433 of them. The other 190 are off by up to a day, 141 later and 49
  earlier (a chat saved again when its window closed, or last saved before the run's final step)
  — and not one of them by a whole number of hours, which is what a wrong zone would look like.
- **The desktop's 435 lines and the Mac's 422** are theirs to convert: after a `git pull` there,
  the first start of MyAgent or SelfBot does it. Until then both viewers show them converted, with
  the note.

## What UTC stamps do not fix

A day row here can still differ from a provider's, for reasons the clock has nothing to do with:

- **A line is one moment** — the end of the run, or for SelfBot the close of the session — while
  the provider books each call when it was made. A run that straddles 00:00 UTC, a run parked
  overnight at an Agent Request, or a SelfBot window left open for days lands on one day here and
  on two or more there. Summed over the days involved, the two sides cover the same calls.
- **The viewers' day rows add every provider and every machine.** A provider's page shows only its
  own spend, so compare a day's rows in the FULL LOG, or the By-provider totals.
- OpenAI's web-search fee never appears in its usage data, so OpenAI runs that search read slightly
  low here.
- The figure is the pricing tables' price for the tokens the API reported, not an invoice.

## Not changed, on purpose

- **Chat file names** keep their local time (`Reconcile_Banking_Transactions_2026-11-24_103002`).
  They are the user's label for a transcript and no provider page shows them; a line's CHAT name
  therefore reads up to eleven hours ahead of its stamp.
- `heartbeat.log`, `unread_summary.log` and the state files stay on the local clock.
- Another machine's log is never rewritten from here (above).

## Verified

- `tests/test_costlog_utc.py` (23 tests): the stamp on Sydney's own rules, written out as a
  `tzinfo` so the tests need no time-zone database and do not depend on the machine they run on —
  both offsets, the last second before and the first after the clocks change, a month and a year
  boundary; the machine's zone against the C library's `mktime`; the conversion on every line shape
  a log holds; the backup, the second pass, a line an old copy appends later, a log changed
  underneath, a failed backup, a failed replace; `resolve_costlog` on the shared and the repo-root
  path, another machine's log left alone; both writers; SelfBot's stand-in for the package-absent
  case held to the original.
- `tests/test_costlog_viewer_days.py`, UtcClockTests (3 tests) and the module's nine older tests
  moved to the UTC clock: the real viewer on logs dated from the UTC day, the clock line, lines
  without the mark converted and noted, the singular.
- Both viewers on the real logs: the same day block, the same note, the same 2,136 rows.
- The macOS viewer cannot run on Windows as it is. It was held to the module's twelve tests under
  Git Bash, with stand-ins for `date -j` (GNU `date -d`), `tail -r` (`tac`) and `less` (`cat`) and
  with awk in `--posix` mode. **Its run under the Mac's own BSD tools is still open** — the
  suite's `tests/test_costlog_viewer_days.py` on the Mac closes it.
- The full suite: 126 modules, 2,568 tests, passing; `ruff check MyAgent.py myagent/` at its
  baseline.

**Not done:** the Console's figures were not pulled and compared number for number — exporting them
is a download, which needs a go-ahead. The comparison to make is the export's `usage_date_utc` rows
against the Anthropic rows of the same UTC day in the FULL LOG.
