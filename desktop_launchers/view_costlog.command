#!/bin/bash
# view_costlog.command — opened by the "API Cost Log" desktop launcher.
#
# Shows the MyAgent/SelfBot API cost log for examination: a spend summary first
# (grand total, today — carrying, since 2026-10-19, its run count and each
# machine's share of the figure — and under it, since 2026-09-22, each of the
# last seven days — this month, then by machine, by provider, by model and
# by instruction — each rollup twice since 2026-09-02: once over all history,
# once over the current month only, under a THIS MONTH heading), then
# every logged run (most recent first) WITH its individual cost, in a
# scrollable, searchable pager. The cost column sits before the model name so
# a narrow terminal wraps only the model tail, never hides the cost (matching
# the Windows twin, where Format-Table used to drop the trailing cost column);
# since 2026-10-25 a row of dashes follows every five runs (RUNS_PER_GROUP),
# so the eye keeps its place in a long log.
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
# on older lines and EMPTY on SelfBot's before 2026-10-14 (since then the
# session's working seconds); the
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
# its own (the blended rate also carries output tokens). The 13th field
# (2026-09-27) is present only on a run that more than one model served —
# MyAgent's Model upgrade from an Agent Request reply on, or an Anthropic
# refusal fallback — whose MODEL field is the model it ENDED on:
# "model,cost,in,out,cache_w,cache_r" per model, joined by "|". The two
# By-model blocks split such a run between its models; every other block and
# the full log read it as one run, as before. The 14th field (2026-10-07) is
# the stem of the chat files the run's transcript is saved under
# (<chat>.json + .txt in the share's saved_chats folder since 2026-10-08, so
# any machine can open it — every MyAgent run names its chat since
# 2026-10-07); whenever it is present the 13th is too, blank for a one-model
# run → the CHAT column, rightmost after INSTRUCTION, blank on older lines
# (SelfBot writes it since 2026-10-14).
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
# empty fields so every merged row is uniformly 15 fields: machine is ALWAYS
# $W (=15), then the chat file stem $14 (2026-10-07 — empty on a line written
# before it, and on SelfBot's), the per-model split $S (=13, 2026-09-27 —
# empty unless more than one model served the run), cache_r $12, cache_w
# $11, out $10, in $9 (the 2026-09-14 token group), calls $8, instruction
# $7, secs $6, params $5
# — any of the middle ten possibly empty. Since 2026-08-16 SelfBot writes an EMPTY secs,
# so it lands in the same shape. W is the one place the merged width lives:
# every awk below takes it as -v W, so a future field costs only W and the
# column list in the FULL LOG awk. NF>=4 keeps the guard the old per-width
# rules gave for free — it drops blank lines and the rotation marker, which a
# bare pad would otherwise turn into a full-width $0.0000 row and count as a
# run.
# The sub() strips the CR that Windows-written lines carry (CRLF via Python
# text mode until 2026-08-03, and any not-yet-updated writer): without it the
# last field ends in \r and the viewer shows ^M after every Windows row.
W=15
S=13
# FULL LOG: a rule of dashes after every this many runs (2026-10-25). The
# Windows twin's $runsPerGroup must match — the two viewers rule the same rows.
RUNS_PER_GROUP=5
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
# rather than with printf's "*" width, which not every awk supports. Keyed on
# the model (3), a row carrying the per-model split ($S, 2026-09-27) counts
# under EACH model that served it, with that model's own cost — the Windows
# twin's Expand-ModelSplit.
bucket() {
  local fmt="    %-$3s \$%10.4f  (%d$4)\n"
  awk -F';' -v K="$1" -v P="$2" -v W="$W" -v S="$S" 'NF>=W && substr($1,1,length(P))==P && $K!="" {
      if (K == 3 && $S != "") {
        np = split($S, parts, "|")
        for (i = 1; i <= np; i++) { if (split(parts[i], p, ",") >= 6) { m[p[1]] += p[2]; c[p[1]]++ } }
      } else { m[$K]+=$4; c[$K]++ }
    }
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
  rows="$(awk -F';' -v W="$W" -v S="$S" "$TOK_FN"'
    NF>=W && $9!="" {
      # A run more than one model served (the per-model split, $S) counts
      # under each of its models, with each model its own buckets.
      if ($S != "") {
        np = split($S, parts, "|")
        for (i = 1; i <= np; i++) {
          if (split(parts[i], p, ",") < 6) continue
          ti[p[1]]+=p[3]; to[p[1]]+=p[4]; tw[p[1]]+=p[5]; tr[p[1]]+=p[6]; c[p[1]]+=p[2]; n[p[1]]++
        }
      } else { ti[$3]+=$9; to[$3]+=$10; tw[$3]+=$11; tr[$3]+=$12; c[$3]+=$4; n[$3]++ }
    }
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
    # The today row's tail (2026-10-19): the run count behind today's figure
    # and each machine's share of it, highest spend first — ranked on the
    # 4-place figure shown, so two machines that print alike are a tie
    # whatever the float noise in their sums (0.1 + 0.2 is not 0.3), the
    # machine name on a tie — "(2 runs: LenovoAug23 $0.1593,
    # Romans-Mac-mini.local $0.0173)" — so a figure this machine did not
    # produce explains itself: the viewer sums EVERY machine's log, and one
    # $0.0173 run on the Mac read as a $0.1766 today when the laptop had run
    # the same instruction at 01:15. "(0 runs)" on a day without one; a
    # machine's figure is its total for the day, its runs are in the FULL LOG
    # below. Count and shares are taken in the summary awk's own pass, on the
    # day key day[] uses, so they are those of the rows day[TODAY] sums by
    # construction, and ranked in its END block — a handful of names, where
    # bucket() ranks MANY keys through sort; awk compares strings by byte,
    # the order LC_ALL=C sort gave, which the Windows twin's name tie-break
    # agrees with on these names. The Windows twin renders the same tail by
    # the same rule.
    # The weekday takes the slot "today" has, padded to its width, so the
    # eight dates stack in one column. ONE money column for those rows and
    # "this month": the amount, "$" included, is right-aligned to end where a
    # single-digit "today" always ended (column 34) — decimal points line up
    # when a day runs past $10, and a < $10 today row is byte-identical to
    # what it was up to that column (its tail follows it). Fixed widths, not
    # printf "*" (see bucket()).
    awk -F';' -v TODAY="$TODAY" -v MONTH="$MONTH" -v DAYS="$DAYS" -v W="$W" '
      NF>=W {
        c=$4+0; total+=c; n++;
        d=substr($1,1,10); day[d]+=c;
        if (d==TODAY) { tn++; tm[$W]+=c }
        if (substr($1,1,7)==MONTH) month+=c;
        if (n==1) first=$1;
        last=$1;
      }
      END {
        printf "  %d runs · $%.4f total\n", n, total;
        printf "  span: %s  →  %s\n", first, last;
        # The machines of today, ranked: a selection sort over nm names on the
        # 4-place figure, highest first, the name (byte order) on a tie.
        nm = 0; for (k in tm) { nm++; name[nm] = k; amt[nm] = sprintf("%.4f", tm[k]) }
        for (i = 1; i <= nm; i++) for (j = i + 1; j <= nm; j++)
          if (amt[j]+0 > amt[i]+0 || (amt[j]+0 == amt[i]+0 && name[j] < name[i])) {
            t = name[i]; name[i] = name[j]; name[j] = t;
            t = amt[i]; amt[i] = amt[j]; amt[j] = t;
          }
        shares = ""; for (i = 1; i <= nm; i++) shares = shares (i > 1 ? ", " : ": ") name[i] " $" amt[i];
        printf "  %-5s (%s):%13s  (%d run%s%s)\n", "today", TODAY, sprintf("$%.4f", day[TODAY]+0), \
          tn, (tn==1 ? "" : "s"), shares;
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
  # CHAT (2026-10-07) is the new last column, so INSTRUCTION now sits
  # mid-row and an empty one renders "-" like the others (column -t would
  # otherwise pull the chat name left into its place); an empty chat is a
  # trailing field, which column -t simply leaves off.
  # A rule of dashes after every RUNS_PER_GROUP runs (2026-10-25, the user's
  # request), so the eye keeps its place in a long log — added by the last awk,
  # AFTER column -t has laid the table out: a dash row fed INTO column -t would
  # be one field, and widen DATE/TIME to its length. Every line is held, the
  # rule is as wide as the widest (a CHAT name runs past the heading, and
  # column -t leaves a row's last field unpadded, so lines differ in length),
  # and it goes before the 6th, 11th, 16th … run — line 1 is the heading — so
  # a table of five or fewer has none and none trails the last run. The
  # Windows twin rules the same rows at the same width.
  { echo "DATE/TIME;MACHINE;PROVIDER;COST(USD);TIME(sec);CALLS;TOK-IN;TOK-OUT;CACHE-W;CACHE-R;MODEL;PARAMETERS;INSTRUCTION;CHAT"
    tail -r "$MERGED" | awk -F';' -v W="$W" "$TOK_FN"'
      NF>=W {
      t=($6=="" ? "-" : $6); k=($8=="" ? "-" : $8); p=($5=="" ? "-" : $5);
      n=($7=="" ? "-" : $7);
      print $1 ";" $W ";" $2 ";" $4 ";" t ";" k ";" \
            tok($9) ";" tok($10) ";" tok($11) ";" tok($12) ";" \
            $3 ";" p ";" n ";" $14 }'; } \
    | column -t -s';' \
    | awk -v N="$RUNS_PER_GROUP" '{ line[NR] = $0; if (length($0) > w) w = length($0) }
        END {
          rule = ""; while (length(rule) < w) rule = rule "-"
          for (i = 1; i <= NR; i++) {
            r = i - 1
            if (r > 1 && (r - 1) % N == 0) print rule
            print line[i]
          }
        }'
} | less -R
