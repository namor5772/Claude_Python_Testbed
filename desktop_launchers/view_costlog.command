#!/bin/bash
# view_costlog.command — opened by the "API Cost Log" desktop launcher.
#
# Shows the MyAgent/SelfBot API cost log for examination: a spend summary first
# (grand total, today, this month, by machine, by provider, by model), then the
# cost per session (consecutive runs on one machine no more than 30 min apart
# count as one working session), then every logged run (most recent first), in
# a scrollable, searchable pager.
#   ↑/↓ scroll · /text search · n next match · q to quit.
#
# Since 2026-08-03 each machine writes its OWN log file into the OneDrive
# share — APICostLog_<machine>.txt in <OneDrive>/MyAppShare (see
# myagent/datapaths.py: per-machine files never conflict-fork, yet OneDrive
# syncs them all everywhere) — so this viewer aggregates EVERY machine's
# spend, not just this clone's. A repo-root APICostLog.txt (no-OneDrive
# fallback, or history an app launch hasn't migrated yet) is included too.
# Each line is "timestamp;provider;model;cost" (semicolon-delimited).
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
  echo "with priced API usage. (Nothing is logged for Ollama, unmatched model"
  echo "prefixes, or STOPped runs.)"
  echo
  read -r -p "Press Return to close this window… " _
  exit 0
fi

# Merge every file into machine-tagged rows "timestamp;provider;model;cost;machine",
# sorted by timestamp — cross-machine order comes from the field, not file order.
MERGED="$(mktemp)"
trap 'rm -f "$MERGED"' EXIT
for i in "${!LOGS[@]}"; do
  awk -F';' -v M="${LABELS[$i]}" 'NF>=4 { print $0 ";" M }' "${LOGS[$i]}"
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
    NF>=5 {
      c=$4+0; total+=c; n++;
      mach[$5]+=c; machn[$5]++;
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
  awk -F';' 'NF>=5 { m[$3]+=$4; c[$3]++ } END { for (k in m) printf "%.4f\t%s\t%d\n", m[k], k, c[k] }' "$MERGED" \
    | sort -rn | awk -F'\t' '{ printf "    %-32s $%10.4f  (%d)\n", $2, $1+0, $3 }'
  echo
  echo "═════════════════════ SESSIONS (most recent first) ═════════════════════"
  echo
  # Cost per session: the log has no session id (MyAgent appends one line per
  # run, SelfBot one per process close), so a "session" is reconstructed by
  # time adjacency — consecutive runs logged by the SAME machine no more than
  # 30 min apart (a scheduled morning batch, an orchestrator plus its waited
  # children, a SelfBot duo's two lines) group into one session. Machine-keyed
  # because the merged rows interleave machines: a Mac run minutes after a
  # Windows run is parallel work, not the same session. BSD awk has no
  # mktime(), so timestamps become epoch seconds via the days-from-civil
  # formula (exact for gap arithmetic; the input is already time-sorted).
  awk -F';' '
    function dayn(y, m, d,    era, yoe, doy, doe) {
      if (m <= 2) { y -= 1; m += 12 }
      era = int(y / 400); yoe = y - era * 400
      doy = int((153 * (m - 3) + 2) / 5) + d - 1
      doe = yoe * 365 + int(yoe / 4) - int(yoe / 100) + doy
      return era * 146097 + doe - 719468
    }
    NF>=5 {
      ts = $1; mach = $5
      ep = dayn(substr(ts,1,4)+0, substr(ts,6,2)+0, substr(ts,9,2)+0) * 86400
      ep += substr(ts,12,2) * 3600 + substr(ts,15,2) * 60 + substr(ts,18,2)
      if (!(mach in last) || ep - last[mach] > 1800) {
        n++; sid[mach] = n; start[n] = substr(ts,1,16); machs[n] = mach
      }
      s = sid[mach]; last[mach] = ep
      endt[s] = substr(ts,12,5); runs[s]++; total[s] += $4
      if (!((s, $3) in seen)) { seen[s, $3] = 1; mods[s] = (mods[s] == "") ? $3 : mods[s] ", " $3 }
    }
    END {
      if (n == 0) { print "  (no sessions)"; exit }
      printf "  %d sessions — consecutive runs on one machine ≤ 30 min apart\n\n", n
      for (i = n; i >= 1; i--) {
        m2 = mods[i]; if (length(m2) > 42) m2 = substr(m2, 1, 41) "…"
        w = (runs[i] == 1) ? "run" : "runs"
        printf "  %s → %s  %-24s $%10.4f  (%d %s; %s)\n", start[i], endt[i], machs[i], total[i], runs[i], w, m2
      }
    }' "$MERGED"
  echo
  echo "═════════════════════ FULL LOG (most recent first) ═════════════════════"
  echo
  { echo "DATE/TIME;MACHINE;PROVIDER;MODEL;COST(USD)"
    tail -r "$MERGED" | awk -F';' 'NF>=5 { print $1 ";" $5 ";" $2 ";" $3 ";" $4 }'; } \
    | column -t -s';'
} | less -R
