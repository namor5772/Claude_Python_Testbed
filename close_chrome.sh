#!/usr/bin/env bash
#
# close_chrome.sh — macOS twin of close_chrome.ps1.
#
# Close ONLY MyAgent's automation browser, leaving personal windows untouched.
#
# Browser-automation instructions call this via run_command to shut the browser
# down cleanly at the end of a run.
#
# Every process it touches must satisfy BOTH tests: the automation profile path
# appears on its command line, AND its executable really is a browser. So a
# personal Brave/Chrome/Edge window open at the same time is never closed, and
# neither is an unrelated process that merely mentions a browser in its
# arguments. MyAgent launches its browser with
# --user-data-dir=~/Library/Application Support/MyAgent/browser_profile on
# macOS (myagent/browser_mixin.py), and that path is this script's default.
#
# Sequence: SIGTERM the matched browser process (Chrome treats SIGTERM as a
# clean shutdown — the equivalent of the Windows twin's CloseMainWindow), poll
# for exit, SIGKILL only the matched leftovers, then reset "exit_type" to
# "Normal" in the profile's Preferences. That last step matters because the
# browser writes exit_type "Crashed" when force-killed, which puts a "Restore
# pages?" bar on top of the page the NEXT automation run needs to click.
#
# Usage:
#   ./close_chrome.sh [--user-data-dir PATH] [--grace SECONDS]
#
# Exit codes: 0 = closed (or nothing to close), 1 = processes survived,
#             2 = bad usage.

USER_DATA_DIR="${HOME}/Library/Application Support/MyAgent/browser_profile"
GRACE_SECONDS=8

while [ $# -gt 0 ]; do
    case "$1" in
        --user-data-dir)
            [ $# -ge 2 ] || { echo "--user-data-dir needs a value" >&2; exit 2; }
            USER_DATA_DIR="$2"; shift 2 ;;
        --grace)
            [ $# -ge 2 ] || { echo "--grace needs a value" >&2; exit 2; }
            GRACE_SECONDS="$2"; shift 2 ;;
        -h|--help)
            sed -n '3,27p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)
            echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

# The browser list is browser_mixin.py's macOS candidates, in its own
# preference order, and lives inline in matched_lines' case arms below rather
# than in a variable: `case $x in $VAR)` would treat the expansion as a single
# pattern, since case alternation is parsed before expansion, so a "|" list in
# a variable would silently stop matching.

# Matching on the profile path is what keeps personal windows safe. Helper
# processes (renderer/GPU) inherit --user-data-dir too and so match as well —
# which is wanted: they are all part of the automation instance.
#
# The browser test is applied to "comm" (the executable path) and NOT to the
# full command line, because a command line can name a browser without being
# one. Testing the whole line was a real bug, proven live on 2026-07-31: a
# shell invoked as `zsh -c '... /Google Chrome/ ... browser_profile ...'` — a
# diagnostic that merely mentioned both strings — matched with no browser
# running at all, and the script SIGTERMed its own caller and then reported
# success. "comm" carries no arguments, so such a shell now reads as /bin/zsh
# and cannot match. This also subsumes the classic "grep matches itself"
# problem; excluding $$ is kept as cheap belt-and-braces.
matched_lines() {
    ps -axo pid=,command= 2>/dev/null \
        | grep -F -- "$USER_DATA_DIR" \
        | awk -v self="$$" '$1 != self { pid = $1; $1 = ""; sub(/^ +/, ""); print pid " " $0 }' \
        | while IFS= read -r line; do
              comm="$(ps -p "${line%% *}" -o comm= 2>/dev/null)"
              case "$comm" in
                  */Brave\ Browser*|*/Google\ Chrome*|*/Microsoft\ Edge*)
                      printf '%s\n' "$line" ;;
              esac
          done
}

matched_pids() { matched_lines | awk '{print $1}'; }

# The top-level browser process is the matched one that is NOT a --type=...
# helper. SIGTERM to it brings the helpers down with it.
main_pids() { matched_lines | grep -v -- '--type=' | awk '{print $1}'; }

pids="$(matched_pids)"
if [ -z "$pids" ]; then
    echo "No automation browser running for profile: $USER_DATA_DIR"
    echo "Nothing closed; any personal browser windows are untouched."
    exit 0
fi
echo "Automation browser processes: $(echo "$pids" | tr '\n' ' ')"

# Graceful shutdown. Fall back to signalling every matched process if no
# top-level one is identifiable (all helpers, or an unexpected command shape).
main="$(main_pids)"
[ -n "$main" ] || main="$pids"
for pid in $main; do
    kill -TERM "$pid" 2>/dev/null
done

# Poll rather than sleeping a flat interval: usually done in well under a second.
deadline=$(( $(date +%s) + GRACE_SECONDS ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    [ -z "$(matched_pids)" ] && break
    sleep 0.5
done

# Force-kill ONLY our own leftovers, never a blanket pkill on the browser name.
left="$(matched_pids)"
if [ -n "$left" ]; then
    echo "Graceful close timed out; force-killing: $(echo "$left" | tr '\n' ' ')"
    for pid in $left; do
        kill -9 "$pid" 2>/dev/null
    done
    sleep 2
fi

# Clear the crash marker so the next run does not get a "Restore pages?" bar.
# BSD sed needs [[:space:]] — GNU's \s is unsupported here — and -i wants an
# explicit empty backup suffix. Unlike the Windows twin there is no BOM hazard
# to work around: sed rewrites the file as-is, whereas PowerShell 5.1's
# Set-Content -Encoding UTF8 would prepend a BOM and make the browser discard
# Preferences as corrupt.
prefs="$USER_DATA_DIR/Default/Preferences"
if [ -f "$prefs" ]; then
    sed -i '' -E 's/"exit_type"[[:space:]]*:[[:space:]]*"[^"]*"/"exit_type":"Normal"/' "$prefs"
    echo "Reset exit_type to Normal in: $prefs"
else
    echo "No Preferences file at $prefs (nothing to reset)."
fi

still="$(matched_pids)"
if [ -n "$still" ]; then
    echo "WARNING: automation browser processes survived:"
    matched_lines
    exit 1
fi

echo "Automation browser closed; personal browser windows untouched."
exit 0
