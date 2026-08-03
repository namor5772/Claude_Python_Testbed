# Desktop Launchers

## macOS

Sources for the seven double-clickable launcher apps; the compiled `.app`
bundles themselves are per-machine and are NOT in git — rebuild them with:

```bash
./desktop_launchers/rebuild.sh
```

The real apps are built into `~/Applications` with Finder **aliases** on the
Desktop. They must not live in `~/Desktop` itself: an app running from a
TCC-protected folder (Desktop/Documents/Downloads) makes macOS pop a
"wants to access your Desktop folder" consent — and again after every
rebuild, since the new code hash makes it a "new" app. Aliases give the
same double-click and icon with no prompt. (The scheduled 7am job is never
affected either way — launchd runs the venv python directly.)

| App | Source | Icon master | What it does |
|---|---|---|---|
| `UnreadSummary.app` | `UnreadSummary.applescript` | `icon_unread_master.png` (anxious envelope, 99+ badge, sunrise) | Runs `UnreadSummary.py` with the venv python; success chime + self-dismissing dialog with the run's log line, error dialog with the log tail on failure. Deliberately TCC-free (notifications would re-ask consent after every rebuild) |
| `CSVEditor.app` | `CSVEditor_launcher.applescript` | `icon_csv_master.png` (googly-eyed comma perched on a spreadsheet) | Launches `CSVEditor.py` detached; if it's already running, brings the window to front instead of starting a second instance |
| `My Agent.app` | `MyAgent_launcher.applescript` | `icon_myagent_master.png` (blue robot face — googly eyes, gold-tipped antenna) | Launches `MyAgent.py` detached. No launch-or-focus: MyAgent is multi-instance by design (each claims the lowest free lock number), so every double-click starts a fresh agent |
| `SelfBot.app` | `SelfBot_launcher.applescript` | `icon_selfbot_master.png` (anxious cross-eyed googly robot; its thought bubble holds a smaller copy of itself, and *its* bubble a smaller copy again — a self-referential Droste recursion) | Launches a NEW `SelfBot.py` instance each time (solo). No launch-or-focus: SelfBot is a two-instance app (the second self-chats with the first), so double-click twice for the pair — SelfBot.py cascades the 2nd window so they don't stack. The auto-positioned side-by-side duo layout is `LaunchSelfBot.bat`'s job |
| `Heartbeat Log.app` | `HeartbeatLog.applescript` (+ `view_heartbeat.command`) | `icon_heartbeat_master.png` (EKG monitor with a googly-eyed heart) | Opens `~/Library/Logs/myagent/heartbeat.log` in a Terminal pager — meaningful events first (idle `nothing found` ticks hidden), then the full log; scrollable & searchable in `less`. A *viewer*, not a runner |
| `API Cost Log.app` | `CostLog.applescript` (+ `view_costlog.command`) | `icon_costlog_master.png` (gold `$` coin with googly eyes on money-green, rising cost bars) | Opens the API cost log in a Terminal pager — aggregating every machine's `APICostLog_<machine>.txt` from `<OneDrive>/MyAppShare` (plus any unmigrated repo-root `APICostLog.txt`): a spend summary first (grand total, today, this month, by machine, by provider, by model), then every run most-recent-first. A *viewer*, not a runner |
| `TodoList.app` | `TodoList_launcher.applescript` | `icon_todolist_master.png` (clipboard with a googly-eyed pencil ticking the last urgent item) | Launches `TodoList.py` detached; launch-or-focus like CSVEditor — TodoList's `todos.json` is OneDrive-synced, so a second local instance would race the first's 5-second sync poll |
| `TodoList (Native).app` | `TodoListNative_launcher.applescript` | `icon_todolist_native_master.png` (the same clipboard with a deep-blue `C++` badge top-left, derived by `make_todolist_native_icon.py`) | Launches `TodoList.exe` — the native C++/Cocoa port built from `TodoList.mm` by `./build_todolist_native.sh` (repo root; run it once per machine, the binary is gitignored). Launch-or-focus across BOTH implementations: it focuses a running `TodoList.exe` *or* `TodoList.py` before launching, since either pair would race the shared 5-second sync poll. Shows a "build it first" dialog if the exe is missing |

`rebuild.sh` patches the repo path into the AppleScript for whatever clone
it runs from, renders the iconset from the 1024px master with `sips`
(no Python needed), compiles with `osacompile`, injects a stable
`CFBundleIdentifier` (`com.roman.launcher.<slug>` — osacompile emits none),
signs, and finally pastes the icon on with `NSWorkspace.setIconForFile` —
that last step outranks macOS's IconServices cache, which can otherwise keep
showing stale artwork no matter how many times Finder restarts. Never run
`xattr -cr` on the built apps afterwards: that strips the pasted-on icon
(re-run `rebuild.sh` if it happens).

**Signing & TCC (why rebuilds no longer break permissions)** — TCC stores a
grant against the app's *designated code requirement*. Under the old ad-hoc
signing that requirement was a hash of the exact binary, so every rebuild
orphaned every granted consent: the toggle in System Settings stayed ON but
`tccd` logged `Failed to match existing code requirement` and silently
denied (symptom on 2026-08-03: MyAgent screenshots came back black in the
Excel area while the Screen Recording toggle looked enabled). Since then
`rebuild.sh` signs with the local self-signed **"Roman Launcher Signing"**
certificate (in `~/Library/Keychains/launcher-signing.keychain-db`,
password `launchersign` — it protects nothing but this key), making the
requirement `identifier + certificate leaf`, which survives rebuilds.
Consents therefore need granting ONCE per machine per app and then stick.
On a machine without the cert, signing falls back to ad-hoc and the old
"re-ask after every rebuild" behaviour returns. To recreate the cert on a
new machine: openssl self-signed cert with the `codeSigning` EKU, exported
`-legacy` PKCS12 (macOS can't import OpenSSL 3's default format), imported
with `-T /usr/bin/codesign` + `set-key-partition-list` into a dedicated
keychain added to the user search list. Note the identifier must never
change once granted — TCC matches on both halves.

**Detachment trap (why the launch lines read `cd X && (nohup ... &)`)** — under
`do shell script`, a trailing `&` on a *compound* list (`cd X && cmd &`) does
NOT detach: the spawned `sh` sits in `wait4()` on the child until the Python app
exits, `do shell script` waits on the `sh`, and the applet never quits (the same
string through a plain `/bin/sh -c` detaches instantly — it's specific to
osascript's spawn context). A still-running applet then swallows every further
double-click, because macOS sends a running app a `reopen` event instead of
launching it again — and an applet blocked inside its `run` handler can't
service events. Symptom (until fixed 2026-07-13): the second press of the
SelfBot alias, which should open the self-chat peer, did nothing (on Windows
each press starts a fresh `pythonw`, so the twins never had the problem).
Backgrounding a *simple* command inside a foreground subshell —
`cd X && (nohup cmd > /dev/null 2>&1 &)` — detaches for real; the applet quits
about a second after launch, so each press is a fresh launch, and the launchers
also carry an `on reopen` handler that launches/focuses directly in case a
second press lands inside that one still-alive second.

**Environment sourcing (why the MyAgent/SelfBot launch lines start with
`. ~/.zshenv > /dev/null 2>&1; . ~/.zshrc > /dev/null 2>&1`)** — macOS GUI apps
(Finder / LaunchServices) start with a bare environment, and `do shell script`
runs **/bin/sh, not zsh**, so no zsh dotfile is sourced automatically. MyAgent
decides which providers to offer from `ANTHROPIC`/`OPENAI`/`GEMINI`/`XAI`
`_API_KEY` at startup, and SelfBot aborts outright without `ANTHROPIC_API_KEY`,
so those two launchers must source the keys in explicitly. BOTH files, because
the keys are split across them: `OPENAI_API_KEY` lives in `~/.zshenv` (moved
there 2026-07 for zsh-invoked launchers in other repos — zsh reads `.zshenv` on
*every* invocation, including `zsh -c`, but /bin/sh never does; the move
silently dropped the OpenAI provider from GUI launches here, while terminal
launches kept working, until both files were sourced 2026-07-17), the rest in
`~/.zshrc`. `.zshenv` is sourced first, matching zsh's own order, and a missing
file is harmless (errors suppressed, `;` continues) — so a key works from
either file. Diagnose with `ps eww <pid> | grep -o '[A-Z_]*_API_KEY'` against a
GUI-launched process; simulate with `env -i HOME="$HOME" /bin/sh -c '...'` — a
plain terminal test inherits your shell's keys and false-passes. The viewers
and CSVEditor need no keys and source nothing. Windows has no equivalent
problem: its env vars are system-wide and inherited by GUI processes.

The **Heartbeat Log** launcher is the odd one out — it opens a *viewer*, not a
Python app. Its `.app` does nothing but `open -a Terminal` the bundled
`view_heartbeat.command`, which pages `~/Library/Logs/myagent/heartbeat.log` in
`less`: the meaningful events first (the idle `nothing found` ticks filtered out
with `grep -v`), then the full log, scrollable and searchable. Going through a
`.command` opened with `open -a Terminal` keeps it TCC-free — a `tell application
"Terminal"` would re-ask an Automation ("control Terminal") consent after every
rebuild. The `.command` itself is machine-independent (it resolves the log under
`$HOME`); only the `.app`'s reference to it stores an absolute repo path, which
`rebuild.sh` patches like the others, and `rebuild.sh` also re-asserts the
`.command`'s executable bit (Terminal refuses to run a non-executable `.command`).
Its icon master is generated by `make_heartbeat_icon.py`
(`python desktop_launchers/make_heartbeat_icon.py`) — an EKG monitor panel with a
glowing green ECG trace and a googly-eyed red heart. Its Windows twin is
`HeartbeatLog_Win.ps1` (on Windows the log is at the repo root, not under a Logs
folder) — see the Windows section.

The **API Cost Log** launcher is the same viewer pattern for the API cost log
(`{timestamp};{provider};{model};{cost}`, semicolon-delimited, gitignored).
Since 2026-08-03 each machine writes its own `APICostLog_<machine>.txt` into
`<OneDrive>/MyAppShare` (per-machine files never conflict-fork, yet OneDrive
syncs them all everywhere — see `myagent/datapaths.py`), so
`view_costlog.command` merges EVERY machine's file (plus any unmigrated
repo-root `APICostLog.txt`) into machine-tagged rows sorted by timestamp, then
`awk -F';'` builds a spend summary — grand total, today, this month, by
machine, by provider, and by model (highest spend first) — before listing
every run most-recent-first **with its individual cost** via `column -t -s';'`.
The cost column sits before the open-ended model name, so a narrow terminal
wraps at worst the model tail and never hides a run's cost — the same layout
the Windows twin uses, where it is load-bearing: `Format-Table -AutoSize`
sizes columns from all rows and silently DROPS trailing columns table-wide
when the widest line exceeds the console width, which made the per-run cost
vanish in a default 80-column conhost the day the MACHINE column was added
(2026-08-03). `CostLog_Win.ps1` therefore renders the run list with explicit
fixed-width format strings (never Format-Table) and widens a narrower-than-100
console best-effort. There are no idle ticks to hide here, so the "meaningful
first" analog is the totals rather than a `grep -v`. Icon master from `make_costlog_icon.py`
(`python desktop_launchers/make_costlog_icon.py`). Its Windows twin is
`CostLog_Win.ps1` (same OneDrive share on Windows) — see the Windows section.

## Windows

`UnreadSummary_Win.ps1` is the Windows twin of `UnreadSummary.applescript`:
it runs `UnreadSummary.py` with the venv python in a hidden window, then a
success chime + self-dismissing dialog with the run's log line (from
`<repo>\unread_summary.log`), or a blocking error dialog with the log tail
on failure. Unlike the AppleScript it needs no path patching — the repo is
resolved from the script's own location — so only the Desktop shortcut is
per-machine. `-DryRun` passes `--dry-run` through for a read-only test.

`icon_unread.ico` is rendered from the same 1024px master (sizes
256/128/64/48/32/16). Regenerate with:

```powershell
.venv\Scripts\python.exe -c "from PIL import Image; Image.open(r'desktop_launchers\icon_unread_master.png').convert('RGBA').save(r'desktop_launchers\icon_unread.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
```

Recreate the Desktop shortcut on a new machine (run from the repo root):

```powershell
$repo = (Get-Location).Path
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Unread Summary.lnk'))
$lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$repo\desktop_launchers\UnreadSummary_Win.ps1`""
$lnk.WorkingDirectory = $repo
$lnk.IconLocation = "$repo\desktop_launchers\icon_unread.ico,0"
$lnk.WindowStyle = 7
$lnk.Save()
```

`CSVEditor_Win.ps1` is the Windows twin of `CSVEditor_launcher.applescript`:
it launches `CSVEditor.py` with the venv **pythonw** (no console window), and if
an instance is already running it brings that window to the front instead of
starting a second copy — the same launch-or-focus behaviour as the AppleScript.
As with the Unread twin the repo is resolved from the script's own location, so
any clone works as-is; only the `.lnk` is per-machine.

`icon_csv.ico` is rendered from the 1024px master `icon_csv_master.png`
(a googly-eyed comma perched on a spreadsheet; sizes 256/128/64/48/32/16). The
master itself is generated by `make_csv_icon.py` (`python desktop_launchers/make_csv_icon.py`);
regenerate the `.ico` from it with:

```powershell
.venv\Scripts\python.exe -c "from PIL import Image; Image.open(r'desktop_launchers\icon_csv_master.png').convert('RGBA').save(r'desktop_launchers\icon_csv.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
```

Recreate the Desktop shortcut on a new machine (run from the repo root):

```powershell
$repo = (Get-Location).Path
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'CSV Editor.lnk'))
$lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$repo\desktop_launchers\CSVEditor_Win.ps1`""
$lnk.WorkingDirectory = $repo
$lnk.IconLocation = "$repo\desktop_launchers\icon_csv.ico,0"
$lnk.WindowStyle = 7
$lnk.Save()
```

### TodoList (Windows shortcut icon)

`icon_todolist.ico` is rendered from the 1024px master
`icon_todolist_master.png` (a clipboard in the app's own palette — light-blue
paper, yellow clip, two ticked-off grey rows and one urgent red row still
pending — with a coral googly-eyed pencil swooping in to tick the last box,
keeping the family gag started by the CSV comma and the SelfBot robot; sizes
256/128/64/48/32/16). The master is generated by `make_todolist_icon.py`
(`python desktop_launchers/make_todolist_icon.py`); regenerate the `.ico` from
it with:

```powershell
.venv\Scripts\python.exe -c "from PIL import Image; Image.open(r'desktop_launchers\icon_todolist_master.png').convert('RGBA').save(r'desktop_launchers\icon_todolist.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
```

There is no `TodoList_Win.ps1` — the Desktop shortcut targets the repo-root
`LaunchTodoList.bat` directly. Recreate it on a new machine (run from the repo
root; note `[Environment]::GetFolderPath('Desktop')` — the Desktop may be
OneDrive-redirected):

```powershell
$repo = (Get-Location).Path
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'TodoList.lnk'))
$lnk.TargetPath = "$repo\LaunchTodoList.bat"
$lnk.WorkingDirectory = $repo
$lnk.IconLocation = "$repo\desktop_launchers\icon_todolist.ico,0"
$lnk.WindowStyle = 7
$lnk.Save()
```

(The tiny repo-root `todolist.ico` predates this icon and is kept only so
older shortcuts that referenced it don't lose their image.)

On macOS the same artwork drives `TodoList.app` (built from
`TodoList_launcher.applescript` + `icon_todolist_master.png` by `rebuild.sh`,
like the other launcher apps — see the table above). The `TodoList (Native)`
variant's `icon_todolist_native_master.png` is *derived* from this master by
`make_todolist_native_icon.py` (imports the base renderer and stamps a `C++`
badge), so a redesign of the base icon regenerates both.

### TodoList (Native) — Windows

`TodoListNative_Win.ps1` is the Windows twin of
`TodoListNative_launcher.applescript`: it launches the repo-root `TodoList.exe`
(the native C++/Win32 port compiled from `TodoList.cpp` by
`.\build_todolist_native.ps1` — gitignored, rebuilt per machine), with
launch-or-focus across **both** TodoList implementations for the same reason as
the AppleScript: a running `TodoList.exe` is focused first, then a running
`TodoList.py`, before anything is launched, since either pair would race the
shared 5-second sync poll on the OneDrive-synced `todos.json`. If the exe has
not been built on this machine yet it shows a "build it first" dialog naming
the build script. Unlike the macOS launcher there is no dotfile sourcing —
Windows env vars (including an optional `TODOLIST_DATA_DIR` override) are
system-wide and inherited by GUI processes. The repo is resolved from the
script's own location; only the `.lnk` is per-machine.

`icon_todolist_native.ico` is rendered from the committed 1024px
`icon_todolist_native_master.png` (the TodoList clipboard with a deep-blue
`C++` badge, derived by `make_todolist_native_icon.py`). The same `.ico` is
also **embedded into `TodoList.exe`** by `TodoList.rc` at build time, so
Explorer, the taskbar, and Alt-Tab show it even without the shortcut.
Regenerate with:

```powershell
.venv\Scripts\python.exe -c "from PIL import Image; Image.open(r'desktop_launchers\icon_todolist_native_master.png').convert('RGBA').save(r'desktop_launchers\icon_todolist_native.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
```

Recreate the Desktop shortcut on a new machine (run from the repo root):

```powershell
$repo = (Get-Location).Path
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'TodoList (Native).lnk'))
$lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$repo\desktop_launchers\TodoListNative_Win.ps1`""
$lnk.WorkingDirectory = $repo
$lnk.IconLocation = "$repo\desktop_launchers\icon_todolist_native.ico,0"
$lnk.WindowStyle = 7
$lnk.Save()
```

`MyAgent_Win.ps1` is the Windows twin of `MyAgent_launcher.applescript`: it
launches `MyAgent.py` with the venv **pythonw** (no console window). Unlike the
CSVEditor twin there is **no** launch-or-focus — MyAgent is multi-instance by
design (each instance claims the lowest free lock number), so every launch
starts a fresh agent. As with the others the repo is resolved from the script's
own location, so any clone works as-is; only the `.lnk` is per-machine.

`icon_myagent.ico` and the master `icon_myagent_master.png` both derive from the
original repo-root `myagent.ico` (256px robot face, upscaled to 1024px with
Lanczos); that repo-root copy is the legacy location, kept for any pre-existing
Windows shortcut. Regenerate the `.ico` from the master with:

```powershell
.venv\Scripts\python.exe -c "from PIL import Image; Image.open(r'desktop_launchers\icon_myagent_master.png').convert('RGBA').save(r'desktop_launchers\icon_myagent.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
```

Recreate the Desktop shortcut on a new machine (run from the repo root):

```powershell
$repo = (Get-Location).Path
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'My Agent.lnk'))
$lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$repo\desktop_launchers\MyAgent_Win.ps1`""
$lnk.WorkingDirectory = $repo
$lnk.IconLocation = "$repo\desktop_launchers\icon_myagent.ico,0"
$lnk.WindowStyle = 7
$lnk.Save()
```

`SelfBot_Win.ps1` is the Windows twin of `SelfBot_launcher.applescript`: it
launches a NEW `SelfBot.py` instance each time with the venv **pythonw** (no console
window). SelfBot is a two-instance app by design — the second instance self-chats
with the first — so, like `MyAgent_Win.ps1` and unlike the CSVEditor twin, there is
deliberately **no** launch-or-focus (a launch-or-focus shortcut could never open the
second window). Double-click twice to get the two self-chatting windows; SelfBot.py
cascades the second window down-right (`CASCADE_OFFSET`) so they don't stack on the
same saved geometry. For the auto-positioned side-by-side duo layout, use
`LaunchSelfBot.bat` at the repo root. As with the others the repo is resolved from
the script's own location, so any clone works as-is; only the `.lnk` is per-machine.

`icon_selfbot.ico` and the master `icon_selfbot_master.png` are generated by
`make_selfbot_icon.py` (`python desktop_launchers/make_selfbot_icon.py`) — an
anxious, cross-eyed googly robot whose thought bubble contains a smaller copy of
itself, whose thought bubble contains a smaller copy again (a self-referential
Droste recursion that never bottoms out — SelfBot's duo self-chat made visual;
sizes 256/128/64/48/32/16). Regenerate the `.ico` from the master with:

```powershell
.venv\Scripts\python.exe -c "from PIL import Image; Image.open(r'desktop_launchers\icon_selfbot_master.png').convert('RGBA').save(r'desktop_launchers\icon_selfbot.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
```

Recreate the Desktop shortcut on a new machine (run from the repo root):

```powershell
$repo = (Get-Location).Path
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'SelfBot.lnk'))
$lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$repo\desktop_launchers\SelfBot_Win.ps1`""
$lnk.WorkingDirectory = $repo
$lnk.IconLocation = "$repo\desktop_launchers\icon_selfbot.ico,0"
$lnk.WindowStyle = 7
$lnk.Save()
```

`HeartbeatLog_Win.ps1` and `CostLog_Win.ps1` are the Windows twins of the two log
**viewers** (`HeartbeatLog.applescript` / `CostLog.applescript`). Unlike every
launcher above they open a **visible** console — they display a log, not run a
hidden task. `HeartbeatLog_Win.ps1` pages `<repo>\heartbeat.log` (meaningful
events first via `-notmatch 'nothing found'`, then the full log);
`CostLog_Win.ps1` aggregates every machine's `;`-delimited
`APICostLog_<machine>.txt` from `<OneDrive>\MyAppShare` (plus any unmigrated
repo-root `APICostLog.txt`) into a spend summary (grand total, today, this
month, by machine, by provider, by model) then lists every run
most-recent-first. Both resolve the repo from the script's own location, page
with `Out-Host -Paging`, and pause on `Read-Host` so the window stays open. On
Windows `heartbeat.log` lives at the repo root (`BASE_DIR / "heartbeat.log"`,
not under a `Logs` folder); the cost log lives in the OneDrive share on both
OSes. Both read the log
with `Get-Content -Encoding UTF8` and set `[Console]::OutputEncoding` to UTF-8
(guarded by `try/catch` for redirected/headless runs): the logs are written UTF-8
by Python (`open(..., encoding="utf-8")`), but Windows PowerShell 5.1 defaults to
the ANSI codepage on both read and display, which would otherwise mojibake the
em-dash in heartbeat's `checked — nothing found` ticks into `â€"`. Verified on
Windows 11 — the cost viewer end-to-end, the heartbeat viewer's parse/encoding
(its interactive `Out-Host -Paging` tail reads keypresses from the console, so it
can't be driven non-interactively, but it shares the cost viewer's display path).

`icon_heartbeat.ico` / `icon_costlog.ico` render from their 1024px masters
(`make_heartbeat_icon.py` / `make_costlog_icon.py`). Regenerate with:

```powershell
.venv\Scripts\python.exe -c "from PIL import Image; Image.open(r'desktop_launchers\icon_heartbeat_master.png').convert('RGBA').save(r'desktop_launchers\icon_heartbeat.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
.venv\Scripts\python.exe -c "from PIL import Image; Image.open(r'desktop_launchers\icon_costlog_master.png').convert('RGBA').save(r'desktop_launchers\icon_costlog.ico', sizes=[(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)])"
```

Recreate the Desktop shortcuts on a new machine (run from the repo root). These
are **viewers**, so each shortcut opens a **visible** window — no
`-WindowStyle Hidden`, and `WindowStyle = 1` (normal) instead of `7`:

```powershell
$repo = (Get-Location).Path
$ws = New-Object -ComObject WScript.Shell
foreach ($v in @(
    @{ Name = 'Heartbeat Log'; Script = 'HeartbeatLog_Win.ps1'; Icon = 'icon_heartbeat.ico' },
    @{ Name = 'API Cost Log';  Script = 'CostLog_Win.ps1';      Icon = 'icon_costlog.ico' }
)) {
    $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) ($v.Name + '.lnk')))
    $lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$repo\desktop_launchers\$($v.Script)`""
    $lnk.WorkingDirectory = $repo
    $lnk.IconLocation = "$repo\desktop_launchers\$($v.Icon),0"
    $lnk.WindowStyle = 1
    $lnk.Save()
}
```
