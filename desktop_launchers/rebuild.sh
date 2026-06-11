#!/bin/bash
# Rebuild the two macOS Desktop launcher apps from the sources in this folder.
#
#   ./rebuild.sh                       # builds both onto ~/Desktop
#   LAUNCHER_DEST=/tmp/x ./rebuild.sh  # build somewhere else (testing)
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
DEST="${LAUNCHER_DEST:-$HOME/Desktop}"

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

build UnreadSummary.applescript      icon_unread_master.png UnreadSummary
build CSVEditor_launcher.applescript icon_csv_master.png    CSVEditor
