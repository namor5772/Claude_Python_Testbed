---
paths:
  - "Account_Activity_WBC.py"
---

## Architecture (Account_Activity_WBC.py)

**Single class design** — Same as the other apps: the `App` class contains all UI, browser automation, HTML parsing, and CSV export logic.

**Browser connection** — Connects to Edge via CDP on port 9222 using Playwright. Searches all open tabs for one containing the target button text. Does not auto-launch Edge — requires the user to start Edge with `--remote-debugging-port=9222` beforehand.

**Threading model** — The click-and-extract loop runs in a background daemon thread (`_click_worker`). A `queue.Queue` passes status messages (info, success, error, done) to the main thread, polled every 50ms via `root.after()`.

**HTML extraction** — After clicking, waits for the DOM row count to stabilise (polling every 1s, up to 30s), then reads the `<tbody data-bind="foreach: PastTransactions()">` element in 50-row JavaScript chunks to avoid Playwright string truncation.

**CSV conversion** — `_convert_html_to_csv()` uses regex to parse WBC's Knockout.js-bound HTML: date from `displayDateOnly` bindings, description from `text: Description` bindings, debit/credit from `IsDebit` conditional blocks, and balance from `account-activity-runningbalance` spans.

**No state persistence** — Unlike SelfBot and MyAgent, this app has no state file. All parameters are set in the UI each run.
