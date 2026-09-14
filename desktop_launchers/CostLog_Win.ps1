# CostLog_Win.ps1 -- Desktop-shortcut viewer for the MyAgent/SelfBot API cost
# log (Windows twin of CostLog.applescript / view_costlog.command). Shows a
# spend summary first (grand total, today, this month, then by machine, by
# provider, by model and by instruction -- each rollup twice since 2026-09-02:
# once over all history, once over the current month only, under a THIS MONTH
# heading), then every run most-recent-first WITH its individual cost -- the
# per-run cost column is rendered with fixed-width format strings, placed
# before the model name, so a narrow console can never drop it (see the FULL
# LOG comment below). Since 2026-08-03 each machine
# writes its OWN log file into the OneDrive share -- APICostLog_<machine>.txt in
# <OneDrive>\MyAppShare (see myagent/datapaths.py: per-machine files never
# conflict-fork, yet OneDrive syncs them all everywhere) -- so this viewer
# aggregates EVERY machine's spend, not just this clone's. A repo-root
# APICostLog.txt (no-OneDrive fallback, or history an app launch hasn't
# migrated yet) is included too. Each line is
# "timestamp;provider;model;cost[;params[;secs[;instruction[;calls]]]]" -- the
# params field (added 2026-08-10: the thinking/temperature settings the run
# used, e.g. "reasoning=Medium, temp=1") is absent on older lines and shown
# blank; the secs field (added 2026-08-12: MyAgent's wall-clock run duration
# in whole seconds -> the TIME(sec) column) is absent on older lines and
# EMPTY on SelfBot's, which doesn't record duration; the instruction field
# (added 2026-08-16: the saved Agent Instruction the run was launched from --
# SelfBot writes its active system prompt's name there -- blank for an ad-hoc
# run -> the INSTRUCTION column, rightmost after PARAMETERS) and the calls
# field (added 2026-08-16: the run's API-call count, the "Call #N" counter --
# one per round-trip of the agentic loop, so beyond the first they are
# tool-use round-trips -> the CALLS column, right of TIME(sec)) are absent on
# older lines and shown blank. The four token fields (9th-12th, added
# 2026-09-14: input / output / cache-write / cache-read, the buckets the
# pricing tables rate -> the TOK-IN / TOK-OUT / CACHE-W / CACHE-R columns,
# compacted to k/M) are absent on older lines and render '-'; they also feed
# the SUMMARY's "By model (tokens and effective blended rate)" block, which
# divides real cost by real tokens -- the only way to see what a model
# ACTUALLY costs per MTok once cache reads are in the mix -- and, since
# 2026-09-15, a CACHE% column: cache reads as a share of the input side, the
# cache effect on its own (the blended rate also carries output tokens).
#
# The desktop shortcut targets a VISIBLE window (it's a viewer, NOT -WindowStyle Hidden):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass
#       -File "<repo>\desktop_launchers\CostLog_Win.ps1"

$repoDir = Split-Path -Parent $PSScriptRoot
$Host.UI.RawUI.WindowTitle = 'API Cost Log'
$inv = [Globalization.CultureInfo]::InvariantCulture
# Widen a narrow console (a fresh shortcut's conhost defaults to 80 columns)
# so the full-log lines don't wrap (190: the 2026-08-16 CALLS + INSTRUCTION
# columns pushed the widest rows past the previous 142; 226 since the
# 2026-09-14 TOKENS group added four 8-wide columns). Best-effort: the
# cost column stays visible even when this fails, because it is placed
# before the model name.
try {
    $rawUI = $Host.UI.RawUI
    if ($rawUI.BufferSize.Width -lt 226) {
        $bs = $rawUI.BufferSize; $bs.Width = 226; $rawUI.BufferSize = $bs
        $ws = $rawUI.WindowSize
        $ws.Width = [Math]::Min(226, $rawUI.MaxPhysicalWindowSize.Width)
        $rawUI.WindowSize = $ws
    }
} catch {}
# The logs are written as UTF-8; read and render them as UTF-8 so a non-ASCII
# provider/model/machine name can't mojibake (guarded: the console-encoding
# setter throws when there is no real console, e.g. a redirected invocation).
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# Same shared-dir resolution as myagent/datapaths.py: MYAGENT_DATA_DIR override,
# else the OneDrive sync root's MyAppShare subfolder.
$share = $env:MYAGENT_DATA_DIR
if (-not $share) {
    foreach ($v in 'OneDrive', 'OneDriveConsumer', 'OneDriveCommercial') {
        $root = [Environment]::GetEnvironmentVariable($v)
        if ($root -and (Test-Path -LiteralPath $root -PathType Container)) {
            $share = Join-Path $root 'MyAppShare'; break
        }
    }
}

# One log file per machine: APICostLog_<machine>.txt in the share (the regex
# excludes the .old rotation archives and .migrated.bak markers), plus the
# repo-root file when it still exists.
$logs = @()
if ($share -and (Test-Path -LiteralPath $share -PathType Container)) {
    $logs += @(Get-ChildItem -LiteralPath $share -File |
        Where-Object { $_.Name -match '^APICostLog_.+\.txt$' } |
        ForEach-Object {
            [pscustomobject]@{
                Path    = $_.FullName
                Machine = $_.Name -replace '^APICostLog_', '' -replace '\.txt$', ''
            }
        })
}
$repoLog = Join-Path $repoDir 'APICostLog.txt'
if (Test-Path -LiteralPath $repoLog) {
    $logs += [pscustomobject]@{ Path = $repoLog; Machine = "$env:COMPUTERNAME (unmigrated)" }
}

# Token counts compacted to <=7 chars so four new columns cost ~36 characters
# of width, not ~48: 1234 -> "1234", 45678 -> "45.7k", 2300000 -> "2.30M". An
# absent field (a pre-2026-09-14 line, or SelfBot before the same change)
# renders '-' rather than 0 -- "not recorded" must not read as "none billed".
function Format-Tok([string]$v) {
    if ([string]::IsNullOrWhiteSpace($v)) { return '-' }
    $n = 0.0
    if (-not [double]::TryParse($v, [ref]$n)) { return '-' }
    if ($n -ge 1000000) { return ('{0:N2}M' -f ($n / 1000000)) }
    if ($n -ge 10000)   { return ('{0:N1}k' -f ($n / 1000)) }
    # No thousands separator below 10k, so this matches the macOS twin's
    # awk "%d" exactly -- the two viewers must render a log line identically.
    return ([string][long]$n)
}

# Per-model token totals + the EFFECTIVE blended rate (total cost / total
# tokens), over rows that actually carry token fields. This is the block that
# answers "what does model X really cost me per MTok" -- the per-token table
# rate can't, because it says nothing about how much of the volume arrived as
# cheap cache reads. Lifetime only (the month-scoped rollups stay cost-only):
# a blended rate over a handful of runs is noise.
function Get-TokenRollup([object[]]$set) {
    $withTok = @($set | Where-Object { $_.TokIn -ne '' })
    if ($withTok.Count -eq 0) { return @() }
    # A positive alignment in a .NET format string already right-aligns
    # ("{2,8}"); there is no '>' flag, and one would throw at render time.
    $thdr = '    {0,-32} {1,5} {2,8} {3,8} {4,8} {5,8} {6,10} {7,9} {8,7}'
    $lines = @('', '  By model (tokens and effective blended rate; runs logged with token counts):')
    $lines += ($thdr -f 'MODEL', 'RUNS', 'IN', 'OUT', 'CACHE-W', 'CACHE-R', 'COST(USD)', '$/MTok', 'CACHE%')
    $lines += @($withTok | Group-Object Model |
        Sort-Object { ($_.Group | Measure-Object Cost -Sum).Sum } -Descending |
        ForEach-Object {
            # PS 5.1's Measure-Object -Property takes a NAME, not a
            # scriptblock (that arrived in PS 6), so the token fields are
            # summed by hand -- and cast defensively, since a 9-field line
            # would leave the later buckets empty.
            $ti = 0.0; $to = 0.0; $tw = 0.0; $tr = 0.0
            foreach ($g in $_.Group) {
                $p = 0.0
                if ([double]::TryParse($g.TokIn,  [ref]$p)) { $ti += $p }
                if ([double]::TryParse($g.TokOut, [ref]$p)) { $to += $p }
                if ([double]::TryParse($g.TokCW,  [ref]$p)) { $tw += $p }
                if ([double]::TryParse($g.TokCR,  [ref]$p)) { $tr += $p }
            }
            $c = ($_.Group | Measure-Object Cost -Sum).Sum
            $all = $ti + $to + $tw + $tr
            $rate = if ($all -gt 0) { '{0:N4}' -f ($c / $all * 1000000) } else { '-' }
            # CACHE% (2026-09-15): cache reads as a share of the INPUT side
            # (in + cache-w + cache-r). The blended rate's denominator also
            # holds output tokens, which bill 5-6x input, so a blended figure
            # under the input sticker can mean cheap cache reads OR little
            # output -- this is the cache effect on its own, needs no pricing,
            # and compares across providers. '-' when no input was recorded.
            $inp = $ti + $tw + $tr
            $share = if ($inp -gt 0) { '{0:N1}%' -f ($tr / $inp * 100) } else { '-' }
            $thdr -f $_.Name, $_.Count, (Format-Tok "$ti"), (Format-Tok "$to"),
                (Format-Tok "$tw"), (Format-Tok "$tr"), ('{0:N4}' -f $c), $rate, $share
        })
    return $lines
}

# One rollup block -- by machine, by provider, by model and (only for rows
# that carry a name) by instruction, each highest spend first -- for a row
# set. Called twice since 2026-09-02: over every row (the lifetime block,
# whose output is unchanged) and over the current month's rows. $qual lands
# in each heading ("By machine (this month):", "By model (this month; highest
# spend first):") so the two blocks stay distinguishable while paging.
function Get-Rollups([object[]]$set, [string]$qual) {
    $sfx  = if ($qual) { " ($qual)" } else { '' }
    $lead = if ($qual) { "$qual; " } else { '' }
    $lines = @('', "  By machine${sfx}:")
    $lines += @($set | Group-Object Machine |
        Sort-Object { ($_.Group | Measure-Object Cost -Sum).Sum } -Descending |
        ForEach-Object { '    {0,-24} ${1,10:N4}  ({2} runs)' -f $_.Name, ($_.Group | Measure-Object Cost -Sum).Sum, $_.Count })
    $lines += @('', "  By provider${sfx}:")
    $lines += @($set | Group-Object Provider |
        Sort-Object { ($_.Group | Measure-Object Cost -Sum).Sum } -Descending |
        ForEach-Object { '    {0,-12} ${1,10:N4}  ({2} runs)' -f $_.Name, ($_.Group | Measure-Object Cost -Sum).Sum, $_.Count })
    $lines += @('', "  By model (${lead}highest spend first):")
    $lines += @($set | Group-Object Model |
        Sort-Object { ($_.Group | Measure-Object Cost -Sum).Sum } -Descending |
        ForEach-Object { '    {0,-32} ${1,10:N4}  ({2})' -f $_.Name, ($_.Group | Measure-Object Cost -Sum).Sum, $_.Count })
    # Only rows that carry an instruction name (2026-08-16 lines onward;
    # ad-hoc runs and older history have none) -- a "(none)" bucket would
    # just restate the block's total for as long as the old lines dominate.
    $named = @($set | Where-Object { $_.Instr -ne '' })
    if ($named.Count -gt 0) {
        $lines += @('', "  By instruction (${lead}highest spend first; runs logged with a name):")
        $lines += @($named | Group-Object Instr |
            Sort-Object { ($_.Group | Measure-Object Cost -Sum).Sum } -Descending |
            ForEach-Object { '    {0,-40} ${1,10:N4}  ({2})' -f $_.Name, ($_.Group | Measure-Object Cost -Sum).Sum, $_.Count })
    }
    return $lines
}

try {
    if ($logs.Count -eq 0) {
        Write-Host "No API cost log found."
        Write-Host "  looked in: $share"
        Write-Host "  and:       $repoLog`n"
        Write-Host "MyAgent.py / SelfBot.py append to APICostLog_<machine>.txt when a run"
        Write-Host "ends with API usage -- Ollama runs log as `$0.0000 lines. (Nothing is"
        Write-Host "logged for a paid provider's unmatched model prefix or a STOP before"
        Write-Host "the first result.)"
        return
    }

    # Parse "timestamp;provider;model;cost[;params[;secs[;instruction[;calls]]]]"
    # from every machine's file, then sort by timestamp -- cross-machine order
    # comes from the field, not file order. Params (5th field, added
    # 2026-08-10), secs (6th, added 2026-08-12), instruction (7th) and calls
    # (8th, both added 2026-08-16) are blank on older lines, as are the four
    # token fields (9th-12th, added 2026-09-14: input / output / cache-write /
    # cache-read, the buckets _get_pricing rates).
    $rows = foreach ($logf in $logs) {
        foreach ($line in Get-Content -LiteralPath $logf.Path -Encoding UTF8) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            $f = $line.Split(';')
            if ($f.Count -lt 4) { continue }   # also skips the rotation marker line
            [pscustomobject]@{
                Time = $f[0]; Provider = $f[1]; Model = $f[2]
                Cost = [double]::Parse($f[3], $inv); Machine = $logf.Machine
                Params = if ($f.Count -ge 5) { $f[4] } else { '' }
                Secs = if ($f.Count -ge 6) { $f[5] } else { '' }
                Instr = if ($f.Count -ge 7) { $f[6] } else { '' }
                Calls = if ($f.Count -ge 8) { $f[7] } else { '' }
                TokIn = if ($f.Count -ge 9) { $f[8] } else { '' }
                TokOut = if ($f.Count -ge 10) { $f[9] } else { '' }
                TokCW = if ($f.Count -ge 11) { $f[10] } else { '' }
                TokCR = if ($f.Count -ge 12) { $f[11] } else { '' }
            }
        }
    }
    $rows = @($rows | Sort-Object Time)

    $srcNames = ($logs | ForEach-Object { Split-Path -Leaf $_.Path }) -join ', '
    $out = @('API Cost Log - all machines', "  sources: $srcNames", ('=' * 76), '', 'SUMMARY')
    if ($rows.Count -eq 0) {
        $out += '  (no priced runs logged yet)'
    } else {
        $total    = ($rows | Measure-Object Cost -Sum).Sum
        $today    = (Get-Date).ToString('yyyy-MM-dd')
        $month    = (Get-Date).ToString('yyyy-MM')
        $monthRows = @($rows | Where-Object { $_.Time.StartsWith($month) })
        $todaySum = [double]($rows | Where-Object { $_.Time.StartsWith($today) } | Measure-Object Cost -Sum).Sum
        $monthSum = [double]($monthRows | Measure-Object Cost -Sum).Sum
        $out += ('  {0} runs - ${1:N4} total' -f $rows.Count, $total)
        $out += ('  span: {0}  ->  {1}' -f $rows[0].Time, $rows[-1].Time)
        $out += ('  today ({0}):      ${1:N4}' -f $today, $todaySum)
        $out += ('  this month ({0}): ${1:N4}' -f $month, $monthSum)
        $out += Get-Rollups $rows ''
        $out += Get-TokenRollup $rows
        # The same four rollups over the current month only (2026-09-02): the
        # lifetime block is dominated by history, so it can't show where THIS
        # month's spend is going.
        $out += @('', "THIS MONTH ($month)")
        if ($monthRows.Count -eq 0) {
            $out += '  (no runs logged this month)'
        } else {
            $out += ('  {0} runs - ${1:N4}' -f $monthRows.Count, $monthSum)
            $out += Get-Rollups $monthRows 'this month'
        }
    }
    $out += @('', ('=' * 21 + ' FULL LOG (most recent first) ' + '=' * 21), '')

    if ($rows.Count -gt 0) {
        # Rendered with explicit fixed-width format strings, NOT Format-Table:
        # -AutoSize sizes columns from ALL rows and, when the widest line
        # exceeds the console width, silently DROPS trailing columns
        # table-wide. Adding the MACHINE column (2026-08-03, the per-machine
        # log merge) pushed the widest rows past a default 80-column conhost
        # and the per-run COST column vanished entirely. Cost now sits BEFORE
        # the open-ended model name, so it stays on screen at any console
        # width -- at worst a long model name wraps.
        # One plain loop for both column widths (headers set the floors), and
        # the row lines are emitted as a single array append -- a per-row
        # `$out +=` reallocates the whole accumulated array each time, which
        # goes quadratic as the merged per-machine logs grow.
        # MODEL and PARAMETERS are fixed-width too now that INSTRUCTION (the
        # open-ended, rightmost column since 2026-08-16 -- the user wants it
        # after PARAMETERS) sits after them; at a too-narrow console the
        # instruction name is the one that wraps -- cost never moves. CALLS
        # (2026-08-16) joins the numeric cluster right of TIME(sec).
        $machW = 7; $provW = 8; $modW = 5; $parW = 10
        foreach ($r in $rows) {
            if ($r.Machine.Length -gt $machW) { $machW = $r.Machine.Length }
            if ($r.Provider.Length -gt $provW) { $provW = $r.Provider.Length }
            if ($r.Model.Length -gt $modW) { $modW = $r.Model.Length }
            if ($r.Params.Length -gt $parW) { $parW = $r.Params.Length }
        }
        # TOKENS (2026-09-14) joins the fixed-width numeric cluster right of
        # CALLS and still LEFT of the open-ended MODEL/PARAMETERS/INSTRUCTION,
        # so the invariant above holds unchanged: cost never moves, and a
        # narrow console wraps the instruction name.
        $fmt = "{0,-19} {1,-$machW} {2,-$provW} {3,9} {4,9} {5,5} {6,8} {7,8} {8,8} {9,8} {10,-$modW} {11,-$parW} {12}"
        $out += ($fmt -f 'DATE/TIME', 'MACHINE', 'PROVIDER', 'COST(USD)', 'TIME(sec)', 'CALLS', 'TOK-IN', 'TOK-OUT', 'CACHE-W', 'CACHE-R', 'MODEL', 'PARAMETERS', 'INSTRUCTION')
        $out += ($fmt -f ('-' * 9), ('-' * 7), ('-' * 8), ('-' * 9), ('-' * 9), ('-' * 5), ('-' * 6), ('-' * 7), ('-' * 7), ('-' * 7), ('-' * 5), ('-' * 10), ('-' * 11))
        $rev = @($rows); [array]::Reverse($rev)
        $out += @(foreach ($r in $rev) {
            $fmt -f $r.Time, $r.Machine, $r.Provider, ('{0:N4}' -f $r.Cost), $r.Secs, $r.Calls,
                (Format-Tok $r.TokIn), (Format-Tok $r.TokOut), (Format-Tok $r.TokCW), (Format-Tok $r.TokCR),
                $r.Model, $r.Params, $r.Instr
        })
    }

    try { $out | Out-Host -Paging } catch { $out | Write-Host }
}
finally {
    Read-Host "`nPress Enter to close"
}
