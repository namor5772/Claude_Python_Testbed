#!/bin/bash
# view_costlog.command — opened by the "API Cost Log" desktop launcher.
#
# Shows the MyAgent/SelfBot API cost log for examination: a spend summary first
# (grand total, today — and under it, since 2026-09-22, each of the last seven
# days — this month, then by machine, by provider, by model and
# by instruction — each rollup twice since 2026-09-02: once over all history,
# once over the current month only, under a THIS MONTH heading), then
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
# TIME(sec)) are absent on older lines. The four token fields (9th–12th, added
# 2026-09-14: input / output / cache-write / cache-read, the buckets the
# pricing tables rate → the TOK-IN / TOK-OUT / CACHE-W / CACHE-R columns,
# compacted to k/M) are absent on older lines and render '-'; they also feed
# the SUMMARY's "By model (tokens and effective blended rate)" block, which
# divides real cost by real tokens — the only way to see what a model ACTUALLY
# costs per MTok once cache reads are in the mix — and, since 2026-09-15, a
# CACHE% column: cache reads as a share of the input side, the cache effect on
# its own (the blended rate also carries output tokens).
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
# "timestamp;provider;model;cost;params;secs;instruction;calls;in;out;cache_w;
#  cache_r;machine", sorted by timestamp — cross-machine order comes from the
# field, not file order. Shorter historic shapes (4-field pre-params, 5-field
# pre-secs, 6-field pre-instruction/calls, 8-field pre-tokens) are padded with
# empty fields so every merged row is uniformly 13 fields: machine is ALWAYS
# $W (=13), then cache_r $12, cache_w $11, out $10, in $9 (the 2026-09-14
# token group), calls $8, instruction $7, secs $6, params $5 — any of the
# middle eight possibly empty. Since 2026-08-16 SelfBot writes an EMPTY secs,
# so it lands in the same shape. W is the one place the merged width lives:
# every awk below takes it as -v W, so a future field costs only W and the
# column list in the FULL LOG awk. NF>=4 keeps the guard the old per-width
# rules gave for free — it drops blank lines and the rotation marker, which a
# bare pad would otherwise turn into a full-width $0.0000 row and count as a
# run.
# The sub() strips the CR that Windows-written lines carry (CRLF via Python
# text mode until 2026-08-03, and any not-yet-updated writer): without it the
# last field ends in \r and the viewer shows ^M after every Windows row.
W=13
MERGED="$(mktemp)"
trap 'rm -f "$MERGED"' EXIT
for i in "${!LOGS[@]}"; do
  awk -F';' -v M="${LABELS[$i]}" -v W="$W" '{ sub(/\r$/, "") }
    NF>=4 { line=$0; for (i=NF; i<W-1; i++) line=line ";"; print line ";" M }' "${LOGS[$i]}"
done | sort -t';' -k1,1 > "$MERGED"

# tok(): a token count compacted to <=7 chars so the four TOKENS columns stay
# narrow — 1234 -> "1234", 45678 -> "45.7k", 2300000 -> "2.30M" — and '-' for
# an absent field ("not recorded", never 0). Defined ONCE here and spliced into
# both awk programs that render counts, because the thresholds must match the
# Windows twin's Format-Tok exactly (the two viewers render a line identically).
TOK_FN='function tok(v) {
  if (v == "") return "-"
  if (v >= 1000000) return sprintf("%.2fM", v / 1000000)
  if (v >= 10000)   return sprintf("%.1fk", v / 1000)
  return sprintf("%d", v)
}'

# One rollup bucket — "    <key> $<sum>  (<count><suffix>)" lines, highest
# spend first — over the merged rows whose timestamp starts with $2 ("" =
# every row), keyed on merged field $1 (2 provider, 3 model, 7 instruction,
# $W machine); $3 pads the key column, $4 is the count suffix (" runs" / "").
# Rows with an empty key are skipped (only ever the instruction field). The
# format string is built in the shell (awk -v turns its \n into a newline)
# rather than with printf's "*" width, which not every awk supports.
bucket() {
  local fmt="    %-$3s \$%10.4f  (%d$4)\n"
  awk -F';' -v K="$1" -v P="$2" -v W="$W" 'NF>=W && substr($1,1,length(P))==P && $K!="" { m[$K]+=$4; c[$K]++ }
    END { for (k in m) printf "%.4f\t%s\t%d\n", m[k], k, c[k] }' "$MERGED" \
    | sort -rn | awk -F'\t' -v FMT="$fmt" '{ printf FMT, $2, $1+0, $3 }'
}

# Per-model token totals + the EFFECTIVE blended rate (total cost / total
# tokens) over rows that actually carry the 2026-09-14 token fields. This is
# what the per-token pricing tables cannot tell you: the table rate says
# nothing about how much of the volume arrived as cheap cache reads, so the
# blended rate is the only honest answer to "what does model X cost me per
# MTok". Lifetime only (the month-scoped rollups stay cost-only) — a blended
# rate over a handful of runs is noise. One awk pass accumulating per-model
# arrays (the bucket() shape), ranked by a leading cost key that cut strips.
token_rollup() {
  local rows
  rows="$(awk -F';' -v W="$W" "$TOK_FN"'
    NF>=W && $9!="" { ti[$3]+=$9; to[$3]+=$10; tw[$3]+=$11; tr[$3]+=$12; c[$3]+=$4; n[$3]++ }
    END {
      for (k in c) {
        all = ti[k] + to[k] + tw[k] + tr[k]
        rate = (all > 0) ? sprintf("%.4f", c[k] / all * 1000000) : "-"
        # CACHE% (2026-09-15): cache reads as a share of the INPUT side
        # (in + cache-w + cache-r), the cache effect on its own. The blended
        # rate above also carries output tokens (5-6x input), so a blended
        # figure under the input sticker can mean cheap reads OR little
        # output; this needs no pricing and compares across providers.
        inp = ti[k] + tw[k] + tr[k]
        share = (inp > 0) ? sprintf("%.1f%%", tr[k] / inp * 100) : "-"
        printf "%.6f\t    %-32s %5d %8s %8s %8s %8s %10.4f %9s %7s\n", \
          c[k], k, n[k], tok(ti[k]), tok(to[k]), tok(tw[k]), tok(tr[k]), c[k], rate, share
      }
    }' "$MERGED" | sort -rn | cut -f2-)"
  [ -n "$rows" ] || return 0
  echo
  echo "  By model (tokens and effective blended rate; runs logged with token counts):"
  printf '    %-32s %5s %8s %8s %8s %8s %10s %9s %7s\n' \
    "MODEL" "RUNS" "IN" "OUT" "CACHE-W" "CACHE-R" "COST(USD)" '$/MTok' 'CACHE%'
  printf '%s\n' "$rows"
}

# One rollup block — by machine, by provider, by model and (only when a row
# carries a name) by instruction — over the rows whose timestamp starts with
# $1. Called twice since 2026-09-02: with "" (the lifetime block) and with
# the current "YYYY-MM" (the THIS MONTH block). $2 is the heading qualifier
# ("" / "this month") so the two blocks stay distinguishable while paging.
rollups() {
  local sfx="" lead=""
  if [ -n "$2" ]; then sfx=" ($2)"; lead="$2; "; fi
  echo
  echo "  By machine${sfx}:"
  bucket "$W" "$1" 24 " runs"
  echo
  echo "  By provider${sfx}:"
  bucket 2 "$1" 12 " runs"
  echo
  echo "  By model (${lead}highest spend first):"
  bucket 3 "$1" 32 ""
  # Only rows that carry an instruction name (2026-08-16 lines onward; ad-hoc
  # runs and older history have none) — a "(none)" bucket would just restate
  # the block's total for as long as the old lines dominate.
  if awk -F';' -v P="$1" -v W="$W" 'NF>=W && substr($1,1,length(P))==P && $7!="" { found=1 } END { exit !found }' "$MERGED"; then
    echo
    echo "  By instruction (${lead}highest spend first; runs logged with a name):"
    bucket 7 "$1" 40 ""
  fi
}

{
  echo "API Cost Log — all machines"
  printf '  sources:'
  for f in "${LOGS[@]}"; do printf ' %s' "${f##*/}"; done
  echo
  echo "════════════════════════════════════════════════════════════════════════════"
  echo
  echo "SUMMARY"
  # One clock reading for every date below: the month and the seven days under
  # "today" are all derived from the TODAY string — read separately, a viewer
  # opened across midnight could show a day twice.
  TODAY="$(date +%Y-%m-%d)"; MONTH="${TODAY%-*}"
  # The last seven days (2026-09-22, the user's request) as "YYYY-MM-DD Dow;"
  # entries, yesterday first — every calendar day, so a day without a run
  # shows as $0.0000. BSD date: -j = do not set the clock, -f parses TODAY
  # (the time of day stays "now"), -v12H moves to noon so a DST shift cannot
  # carry the result across midnight, -v-Nd steps back N calendar days.
  # LC_ALL=C: English weekday names, the same ones the Windows twin prints.
  DAYS=""
  for back in 1 2 3 4 5 6 7; do
    DAYS="$DAYS$(LC_ALL=C date -j -v12H -v-"$back"d -f %Y-%m-%d "$TODAY" '+%Y-%m-%d %a' 2>/dev/null);"
  done
  if awk -F';' -v W="$W" 'NF>=W { found=1 } END { exit !found }' "$MERGED"; then
    # The weekday takes the slot "today" has, padded to its width, so the
    # eight dates stack in one column. ONE money column for those rows and
    # "this month": the amount, "$" included, is right-aligned to end where a
    # single-digit "today" always ended (column 34) — decimal points line up
    # when a day runs past $10, and a < $10 today row is byte-identical to
    # what it was. Fixed widths, not printf "*" (see bucket()).
    awk -F';' -v TODAY="$TODAY" -v MONTH="$MONTH" -v DAYS="$DAYS" -v W="$W" '
      NF>=W {
        c=$4+0; total+=c; n++;
        day[substr($1,1,10)]+=c;
        if (substr($1,1,7)==MONTH) month+=c;
        if (n==1) first=$1;
        last=$1;
      }
      END {
        printf "  %d runs · $%.4f total\n", n, total;
        printf "  span: %s  →  %s\n", first, last;
        printf "  %-5s (%s):%13s\n", "today", TODAY, sprintf("$%.4f", day[TODAY]+0);
        nd = split(DAYS, dd, ";");
        for (i=1; i<=nd; i++) {
          if (dd[i]=="") continue;
          split(dd[i], p, " ");
          printf "  %-5s (%s):%13s\n", p[2], p[1], sprintf("$%.4f", day[p[1]]+0);
        }
        printf "  this month (%s):%11s\n", MONTH, sprintf("$%.4f", month+0);
      }' "$MERGED"
    rollups "" ""
    token_rollup
    # The same four rollups over the current month only (2026-09-02): the
    # lifetime block is dominated by history, so it can't show where THIS
    # month's spend is going.
    echo
    echo "THIS MONTH ($MONTH)"
    if awk -F';' -v MONTH="$MONTH" -v W="$W" 'NF>=W && substr($1,1,7)==MONTH { found=1 } END { exit !found }' "$MERGED"; then
      awk -F';' -v MONTH="$MONTH" -v W="$W" 'NF>=W && substr($1,1,7)==MONTH { s+=$4; n++ }
        END { printf "  %d runs · $%.4f\n", n, s }' "$MERGED"
      rollups "$MONTH" "this month"
    else
      echo "  (no runs logged this month)"
    fi
  else
    echo "  (no priced runs logged yet)"
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
  # line, ad-hoc run) just leaves the tail blank. CALLS (2026-08-16) and the
  # four TOKENS columns (2026-09-14) join the numeric cluster right of
  # TIME(sec), still left of the open-ended MODEL/PARAMETERS/INSTRUCTION, so
  # cost never moves. An empty secs (SelfBot / older line), calls (older
  # line), params (pre-2026-08-10 line) or token field (pre-2026-09-14 line)
  # renders as "-" — they sit mid-row and BSD column -t COLLAPSES consecutive
  # delimiters, so a genuinely empty field would shift every later column
  # left. That is why tok() returns "-" and not "" for an absent count.
  { echo "DATE/TIME;MACHINE;PROVIDER;COST(USD);TIME(sec);CALLS;TOK-IN;TOK-OUT;CACHE-W;CACHE-R;MODEL;PARAMETERS;INSTRUCTION"
    tail -r "$MERGED" | awk -F';' -v W="$W" "$TOK_FN"'
      NF>=W {
      t=($6=="" ? "-" : $6); k=($8=="" ? "-" : $8); p=($5=="" ? "-" : $5);
      print $1 ";" $W ";" $2 ";" $4 ";" t ";" k ";" \
            tok($9) ";" tok($10) ";" tok($11) ";" tok($12) ";" \
            $3 ";" p ";" $7 }'; } \
    | column -t -s';'
} | less -R
