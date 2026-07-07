#!/bin/bash
# refresh_launcher_icons.command — restore the custom icons on the launcher
# Desktop aliases in one shot.
#
# Why this exists: on an iCloud-synced Desktop, *launching* a shortcut can strip
# its custom icon — iCloud drops the icon's resource fork / clears the
# "custom icon" (kHasCustomIcon) flag on launch, and won't honor a `uchg` lock
# that would prevent it (the lock itself gets removed too). There is no reliable
# way to stop it; this just re-applies every launcher icon + flag and refreshes
# Finder. Double-click whenever an icon has reverted to a generic/default look.
#
# Resolves the icon masters from this script's own folder, so it works from any
# clone. Pairs must match rebuild.sh's alias list, plus launchers built outside
# this repo (Numc lives in ~/projects/numc; only its icon master is kept here).
DIR="$(cd "$(dirname "$0")" && pwd)"
n=0
while IFS='|' read -r name png; do
  P="$HOME/Desktop/$name"
  [ -e "$P" ] || { echo "  skip (no alias): $name"; continue; }
  chflags nouchg "$P" 2>/dev/null          # ensure writable (clear any stale lock)
  osascript -l JavaScript \
    -e "ObjC.import('AppKit');" \
    -e "const i = \$.NSImage.alloc.initWithContentsOfFile('$DIR/$png');" \
    -e "\$.NSWorkspace.sharedWorkspace.setIconForFileOptions(i, '$P', 0);" >/dev/null 2>&1
  SetFile -a C "$P" 2>/dev/null             # force the kHasCustomIcon flag (iCloud clears it)
  echo "  refreshed: $name"
  n=$((n + 1))
done <<'PAIRS'
UnreadSummary|icon_unread_master.png
CSVEditor|icon_csv_master.png
My Agent|icon_myagent_master.png
Heartbeat Log|icon_heartbeat_master.png
API Cost Log|icon_costlog_master.png
Numc|icon_numc_master.png
PAIRS
killall Finder 2>/dev/null                  # force Finder to re-read the icons
echo "Done — refreshed $n launcher icon(s) and relaunched Finder. You can close this window."
