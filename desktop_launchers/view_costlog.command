#!/bin/bash
# view_costlog.command — opened by the "API Cost Log" desktop launcher.
#
# Shows the MyAgent/SelfBot API cost log for examination: a spend summary first
# (grand total, today, this month, by machine, by provider, by model), then
# every logged run (most recent first) WITH its individual cost, in a
# scrollable, searchable pager. The cost column sits before the model name so
# a narrow terminal wraps only the model tail, never hides the cost (matching
# the Windows twin, where Format-Table used to drop the trailing cost column).
#   ↑/↓ scroll · /text search · n next match · q to quit.
#
# Since 2026-08-03 each machine writes its OWN log file into the OneDrive
# share — APICostLog_<machine>.txt in <OneDrive>/MyAppShare (see
# myagent/datapaths.py: per-machine files never conflict-fork, yet OneDrive
# syncs them all everywhere) — so this viewer aggregates EVERY machine's
# spend, not just this clone's. A repo-root APICostLog.txt (no-OneDrive
# fallback, or history an app launch hasn't migrated yet) is included too.
# Each line is
# "timestamp;provider;model;cost[;params[;secs[;instruction[;calls]]]]"
# (semicolon-delimited); the params field (added 2026-08-10: the thinking/
# temperature settings the run used, e.g. "reasoning=Medium, temp=1") is
# absent on older lines; the secs field (added 2026-08-12: MyAgent's
# wall-clock run duration, whole seconds → the TIME(sec) column) is absent
# on older lines and EMPTY on SelfBot's, which doesn't record duration; the
# instruction field (added 2026-08-16: the saved Agent Instruction the run
# was launched from — SelfBot writes its active system prompt's name there —
# blank for an ad-hoc run → the INSTRUCTION column, rightmost after
# PARAMETERS) and the calls field (added 2026-08-16: the run's API-call
# count, the "Call #N" counter — one per round-trip of the agentic loop, so
# beyond the first they are tool-use round-trips → the CALLS column, right of
# TIME(sec)) are absent on older lines.
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$DIR")"

# Same shared-dir resolution as myagent/datapaths.py: MYAGENT_DATA_DIR
# override, else the OneDrive File Provider root (~/Library/CloudStorage/
# OneDrive-*, preferring -Personal; legacy ~/OneDrive) + /MyAppShare.
SHARE="${MYAGENT_DATA_DIR:-}"
if [ -z "$SHARE" ]; then
  for cand in "$HOME/Library/CloudStorage/OneDrive-Personal" \
              "$HOME"/Library/CloudStorage/OneDrive-* \
              "$HOME/OneDrive"; do
    if [ -d "$cand" ]; then SHARE="$cand/MyAppShare"; break; fi
  done
fi

# One log file per machine (the glob can't match the .old rotation archives or
# .migrated.bak markers), plus the repo-root file when it still exists.
LOGS=()
LABELS=()
if [ -n "$SHARE" ] && [ -d "$SHARE" ]; then
  for f in "$SHARE"/APICostLog_*.txt; do
    [ -f "$f" ] || continue
    b="${f##*/}"; b="${b#APICostLog_}"; b="${b%.txt}"
    LOGS+=("$f"); LABELS+=("$b")
  done
fi
if [ -f "$REPO/APICostLog.txt" ]; then
  LOGS+=("$REPO/APICostLog.txt"); LABELS+=("$(hostname -s)(unmigrated)")
fi

if [ ${#LOGS[@]} -eq 0 ]; then
  echo "No API cost log found."
  echo "  looked in: ${SHARE:-<no OneDrive share found>}"
  echo "  and:       $REPO/APICostLog.txt"
  echo
  echo "MyAgent.py / SelfBot.py append to APICostLog_<machine>.txt when a run ends"
  echo "with API usage — Ollama runs log as \$0.0000 lines. (Nothing is logged for"
  echo "a paid provider's unmatched model prefix or a STOP before the first result.)"
  echo
  read -r -p "Press Return to close this window… " _
  exit 0
fi

# Merge every file into machine-tagged rows
# "timestamp;provider;model;cost;params;secs;instruction;calls;machine",
# sorted by timestamp — cross-machine order comes from the field, not file
# order. Shorter historic shapes (4-field pre-params, 5-field pre-secs,
# 6-field pre-instruction/calls) get empty fields appended so every merged
# row is uniformly 9 fields (machine is ALWAYS $9, calls $8, instruction $7,
# secs $6, params $5 — the middle four possibly empty). Since 2026-08-16
# SelfBot writes 8 fields with an EMPTY secs, so it lands in the same shape.
# The sub() strips the CR that Windows-written lines carry (CRLF via Python
# text mode until 2026-08-03, and any not-yet-updated writer): without it the
# last field ends in \r and the viewer shows ^M after every Windows row.
MERGED="$(mktemp)"
trap 'rm -f "$MERGED"' EXIT
for i in "${!LOGS[@]}"; do
  awk -F';' -v M="${LABELS[$i]}" '{ sub(/\r$/, "") }
    NF==4 { print $0 ";;;;;" M }
    NF==5 { print $0 ";;;;" M }
    NF==6 { print $0 ";;;" M }
    NF==7 { print $0 ";;" M }
    NF>=8 { print $0 ";" M }' "${LOGS[$i]}"
done | sort -t';' -k1,1 > "$MERGED"

{
  echo "API Cost Log — all machines"
  printf '  sources:'
  for f in "${LOGS[@]}"; do printf ' %s' "${f##*/}"; done
  echo
  echo "════════════════════════════════════════════════════════════════════════════"
  echo
  echo "SUMMARY"
  awk -F';' -v TODAY="$(date +%Y-%m-%d)" -v MONTH="$(date +%Y-%m)" '
    NF>=9 {
      c=$4+0; total+=c; n++;
      mach[$9]+=c; machn[$9]++;
      prov[$2]+=c; provn[$2]++;
      d=substr($1,1,10); mo=substr($1,1,7);
      if (d==TODAY) today+=c;
      if (mo==MONTH) month+=c;
      if (n==1) first=$1;
      last=$1;
    }
    END {
      if (n==0) { print "  (no priced runs logged yet)"; exit }
      printf "  %d runs · $%.4f total\n", n, total;
      printf "  span: %s  →  %s\n", first, last;
      printf "  today (%s):      $%.4f\n", TODAY, today+0;
      printf "  this month (%s): $%.4f\n", MONTH, month+0;
      print "";
      print "  By machine:";
      for (m in mach) printf "    %-24s $%10.4f  (%d runs)\n", m, mach[m], machn[m];
      print "";
      print "  By provider:";
      for (p in prov) printf "    %-12s $%10.4f  (%d runs)\n", p, prov[p], provn[p];
    }' "$MERGED"
  echo
  echo "  By model (highest spend first):"
  awk -F';' 'NF>=9 { m[$3]+=$4; c[$3]++ } END { for (k in m) printf "%.4f\t%s\t%d\n", m[k], k, c[k] }' "$MERGED" \
    | sort -rn | awk -F'\t' '{ printf "    %-32s $%10.4f  (%d)\n", $2, $1+0, $3 }'
  # Only rows that carry an instruction name (2026-08-16 lines onward; ad-hoc
  # runs and older history have none) — a "(none)" bucket would just restate
  # the grand total for as long as the old lines dominate.
  if awk -F';' 'NF>=9 && $7!="" { found=1 } END { exit !found }' "$MERGED"; then
    echo
    echo "  By instruction (highest spend first; runs logged with a name):"
    awk -F';' 'NF>=9 && $7!="" { m[$7]+=$4; c[$7]++ } END { for (k in m) printf "%.4f\t%s\t%d\n", m[k], k, c[k] }' "$MERGED" \
      | sort -rn | awk -F'\t' '{ printf "    %-40s $%10.4f  (%d)\n", $2, $1+0, $3 }'
  fi
  echo
  echo "═════════════════════ FULL LOG (most recent first) ═════════════════════"
  echo
  # COST/TIME before MODEL: the numeric columns stay left of the open-ended
  # ones, so a narrow terminal wraps at worst the model/params tail — the
  # per-run cost is always on screen (the Windows twin does the same; its
  # Format-Table used to silently drop the trailing cost column instead).
  # INSTRUCTION is last (2026-08-16, the user wants it after PARAMETERS):
  # the open-ended column takes the wrap, and an empty instruction (older
  # line, ad-hoc run) just leaves the tail blank. CALLS (2026-08-16) joins
  # the numeric cluster right of TIME(sec). An empty secs (SelfBot / older
  # line), calls (older line) or params (pre-2026-08-10 line) renders as
  # "-" — they sit mid-row and BSD column -t COLLAPSES consecutive
  # delimiters, so a genuinely empty field would shift every later column
  # left.
  { echo "DATE/TIME;MACHINE;PROVIDER;COST(USD);TIME(sec);CALLS;MODEL;PARAMETERS;INSTRUCTION"
    tail -r "$MERGED" | awk -F';' 'NF>=9 {
      t=($6=="" ? "-" : $6); k=($8=="" ? "-" : $8); p=($5=="" ? "-" : $5);
      print $1 ";" $9 ";" $2 ";" $4 ";" t ";" k ";" $3 ";" p ";" $7 }'; } \
    | column -t -s';'
} | less -R
