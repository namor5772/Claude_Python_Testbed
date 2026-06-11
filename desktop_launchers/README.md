# Desktop Launchers (macOS)

Sources for the two double-clickable launcher apps; the compiled `.app`
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
| `CSVEditor.app` | `CSVEditor_launcher.applescript` | `icon_csv_master.png` (sunglasses semicolon, deposed comma, spreadsheet) | Launches `CSVEditor.py` detached; if it's already running, brings the window to front instead of starting a second instance |

`rebuild.sh` patches the repo path into the AppleScript for whatever clone
it runs from, renders the iconset from the 1024px master with `sips`
(no Python needed), compiles with `osacompile`, ad-hoc signs, and finally
pastes the icon on with `NSWorkspace.setIconForFile` — that last step
outranks macOS's IconServices cache, which can otherwise keep showing stale
artwork no matter how many times Finder restarts. Never run `xattr -cr` on
the built apps afterwards: that strips the pasted-on icon (re-run
`rebuild.sh` if it happens). First press after a rebuild re-asks the
one-time permission prompts (notifications / System Events) because the
code hash changed.
