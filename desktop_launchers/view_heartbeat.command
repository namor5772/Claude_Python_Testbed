#!/bin/bash
# view_heartbeat.command — opened by the "Heartbeat Log" desktop launcher.
#
# Shows Heartbeat.py's log for examination: the meaningful events first (the idle
# "nothing found" ticks hidden via grep -v), then the full log, in a scrollable,
# searchable pager.  ↑/↓ scroll · /text search · n next match · q to quit.
#
# Machine-independent (resolves the log under $HOME), so it needs no path
# patching — only the .app that opens it stores an absolute path.
LOG="$HOME/Library/Logs/myagent/heartbeat.log"

if [ ! -f "$LOG" ]; then
  echo "Heartbeat log not found:"
  echo "  $LOG"
  echo
  echo "Heartbeat.py creates it on its first launchd tick (it polls every 5 min)."
  echo
  read -r -p "Press Return to close this window… " _
  exit 0
fi

total=$(grep -c '' "$LOG")
events=$(grep -vc 'nothing found' "$LOG")

{
  echo "Heartbeat log — $LOG"
  echo "$total ticks logged · $events meaningful events shown below (idle ticks hidden)"
  echo "════════════════════════════════════════════════════════════════════════════"
  echo
  grep -v 'nothing found' "$LOG"
  echo
  echo "═══════════════════ FULL LOG (every tick, newest at the end) ════════════════"
  echo
  cat "$LOG"
} | less -R
