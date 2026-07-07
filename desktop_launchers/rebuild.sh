#!/bin/bash
# Rebuild the macOS launcher apps from the sources in this folder.
#
#   ./rebuild.sh                       # builds into ~/Applications + Desktop aliases
#   LAUNCHER_DEST=/tmp/x ./rebuild.sh  # build somewhere else (testing; no aliases)
#
# The real apps live in ~/Applications, NOT ~/Desktop: an app residing in a
# TCC-protected folder (Desktop/Documents/Downloads) triggers a "wants to
# access your Desktop folder" consent every time it becomes a "new" app
# (i.e. after every rebuild). The Desktop gets Finder ALIASES instead —
# same double-click, same icon, no protected-folder prompt.
#
# The .applescript sources store the M4 Mac Mini's repo path; this script
# rewrites it to wherever THIS clone lives before compiling, so the repo's
# no-hardcoded-paths rule holds on any machine. Icons are applied twice:
# applet.icns inside the bundle (the proper mechanism) AND as a pasted-on
# Finder custom icon via NSWorkspace — the latter outranks macOS's
# IconServices cache, which otherwise loves to keep serving stale artwork.
# Order matters: xattr -cr (clean) -> codesign -> setIcon LAST, because
# xattr -cr is exactly the command that deletes a pasted-on icon.
#
# First press of a rebuilt app re-asks any TCC consents (notifications /
# System Events control) — the code hash changed, so macOS treats it as new.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$DIR")"
DEST="${LAUNCHER_DEST:-$HOME/Applications}"
mkdir -p "$DEST"

# The log-viewer apps open .command helpers via `open -a Terminal`; Terminal only
# runs a .command that is executable, so guarantee the bit survives a fresh clone.
chmod +x "$DIR"/*.command 2>/dev/null || true

build() { # <source.applescript> <master.png> <AppName>
  local src="$1" png="$2" name="$3"
  local app="$DEST/$name.app"
  local tmp; tmp="$(mktemp -d)"

  sed "s|/Users/roman/projects/Claude_Python_Testbed|$REPO|g" \
      "$DIR/$src" > "$tmp/src.applescript"

  mkdir "$tmp/icon.iconset"
  for s in 16 32 64 128 256 512; do
    sips -z "$s" "$s" "$DIR/$png" \
         --out "$tmp/icon.iconset/icon_${s}x${s}.png" >/dev/null
    sips -z "$((s*2))" "$((s*2))" "$DIR/$png" \
         --out "$tmp/icon.iconset/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$tmp/icon.iconset" -o "$tmp/icon.icns"

  rm -rf "$app"
  osacompile -o "$app" "$tmp/src.applescript"
  cp "$tmp/icon.icns" "$app/Contents/Resources/applet.icns"
  xattr -cr "$app"
  codesign --force --deep -s - "$app"
  osascript -l JavaScript \
    -e "ObjC.import('AppKit');" \
    -e "const i = \$.NSImage.alloc.initWithContentsOfFile('$DIR/$png');" \
    -e "\$.NSWorkspace.sharedWorkspace.setIconForFileOptions(i, '$app', 0);" \
    >/dev/null
  rm -rf "$tmp"
  echo "built $app"
}

build UnreadSummary.applescript      icon_unread_master.png    UnreadSummary
build CSVEditor_launcher.applescript icon_csv_master.png       CSVEditor
build MyAgent_launcher.applescript   icon_myagent_master.png   "My Agent"
build SelfBot_launcher.applescript   icon_selfbot_master.png   SelfBot
build HeartbeatLog.applescript       icon_heartbeat_master.png "Heartbeat Log"
build CostLog.applescript            icon_costlog_master.png   "API Cost Log"

# Desktop aliases (only for the real ~/Applications install, not test builds)
if [ "$DEST" = "$HOME/Applications" ]; then
  while IFS='|' read -r name png; do
    # Create the alias only if missing — recreating it every run churns icon
    # positions and, on an iCloud-synced Desktop, races the file provider into
    # "<name> 2" conflict copies (observed 2026-06-14). Never `xattr -c` an alias
    # to "reset" it: that also strips the FinderInfo alias flag and the file stops
    # being recognised as an alias.
    if [ ! -e "$HOME/Desktop/$name" ]; then
      osascript -e "tell application \"Finder\"" \
                -e "set a to make alias file at desktop to (POSIX file \"$DEST/$name.app\")" \
                -e "set name of a to \"$name\"" \
                -e "end tell" >/dev/null
      echo "alias  $HOME/Desktop/$name (created)"
    else
      echo "alias  $HOME/Desktop/$name (exists; re-pasting icon)"
    fi
    # (Re-)paste the icon on EVERY run — modifying the existing alias in place, so
    # positions are preserved and there's no "<name> 2" race. Necessary because (a)
    # alias creation snapshots a stale/generic target icon until IconServices
    # re-indexes, and (b) an iCloud-synced Desktop strips an alias's pasted-on icon
    # after it's launched. So re-running rebuild.sh is the no-churn way to restore a
    # launcher icon that has gone blank/generic. (A bare alias with no pasted icon
    # renders generic on an iCloud Desktop — confirmed 2026-06-14 — so this paste
    # is mandatory, not cosmetic.)
    osascript -l JavaScript \
      -e "ObjC.import('AppKit');" \
      -e "const i = \$.NSImage.alloc.initWithContentsOfFile('$DIR/$png');" \
      -e "\$.NSWorkspace.sharedWorkspace.setIconForFileOptions(i, '$HOME/Desktop/$name', 0);" \
      >/dev/null
    # setIcon writes the icon data but, on an iCloud-synced Desktop, doesn't
    # reliably set the kHasCustomIcon flag — and without that flag Finder draws a
    # generic icon (confirmed 2026-06-14). Force it on. (Do NOT chflags uchg here:
    # the lock races the icon write for large icons and iCloud removes it anyway.)
    SetFile -a C "$HOME/Desktop/$name" 2>/dev/null || true
  done <<'PAIRS'
UnreadSummary|icon_unread_master.png
CSVEditor|icon_csv_master.png
My Agent|icon_myagent_master.png
SelfBot|icon_selfbot_master.png
Heartbeat Log|icon_heartbeat_master.png
API Cost Log|icon_costlog_master.png
PAIRS
fi
