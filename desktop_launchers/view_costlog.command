#!/bin/bash
# view_costlog.command — opened by the "API Cost Log" desktop launcher.
#
# Shows MyAgent's API cost log for examination: a spend summary first (grand
# total, today, this month, by provider, by model), then every logged run
# (most recent first), in a scrollable, searchable pager.
#   ↑/↓ scroll · /text search · n next match · q to quit.
#
# APICostLog.txt lives at the repo ROOT (not under $HOME), so resolve the repo
# from this script's own location — works from any clone with no path patching.
# Each line is "timestamp;provider;model;cost" (semicolon-delimited).
DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$DIR")"
LOG="$REPO/APICostLog.txt"

if [ ! -f "$LOG" ]; then
  echo "API cost log not found:"
  echo "  $LOG"
  echo
  echo "MyAgent.py appends to it when a run ends with priced API usage."
  echo "(Nothing is logged for Ollama, unmatched model prefixes, or STOPped runs.)"
  echo
  read -r -p "Press Return to close this window… " _
  exit 0
fi

{
  echo "API Cost Log — $LOG"
  echo "════════════════════════════════════════════════════════════════════════════"
  echo
  echo "SUMMARY"
  awk -F';' -v TODAY="$(date +%Y-%m-%d)" -v MONTH="$(date +%Y-%m)" '
    NF>=4 {
      c=$4+0; total+=c; n++;
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
      print "  By provider:";
      for (p in prov) printf "    %-12s $%10.4f  (%d runs)\n", p, prov[p], provn[p];
    }' "$LOG"
  echo
  echo "  By model (highest spend first):"
  awk -F';' 'NF>=4 { m[$3]+=$4; c[$3]++ } END { for (k in m) printf "%.4f\t%s\t%d\n", m[k], k, c[k] }' "$LOG" \
    | sort -rn | awk -F'\t' '{ printf "    %-32s $%10.4f  (%d)\n", $2, $1+0, $3 }'
  echo
  echo "═════════════════════ FULL LOG (most recent first) ═════════════════════"
  echo
  { echo "DATE/TIME;PROVIDER;MODEL;COST(USD)"; tail -r "$LOG" | awk 'NF'; } | column -t -s';'
} | less -R
