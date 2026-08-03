# CostLog_Win.ps1 -- Desktop-shortcut viewer for the MyAgent/SelfBot API cost
# log (Windows twin of CostLog.applescript / view_costlog.command). Shows a
# spend summary first (grand total, today, this month, by machine, by provider,
# by model), then every run most-recent-first WITH its individual cost -- the
# per-run cost column is rendered with fixed-width format strings, placed
# before the model name, so a narrow console can never drop it (see the FULL
# LOG comment below). Since 2026-08-03 each machine
# writes its OWN log file into the OneDrive share -- APICostLog_<machine>.txt in
# <OneDrive>\MyAppShare (see myagent/datapaths.py: per-machine files never
# conflict-fork, yet OneDrive syncs them all everywhere) -- so this viewer
# aggregates EVERY machine's spend, not just this clone's. A repo-root
# APICostLog.txt (no-OneDrive fallback, or history an app launch hasn't
# migrated yet) is included too. Each line is "timestamp;provider;model;cost".
#
# The desktop shortcut targets a VISIBLE window (it's a viewer, NOT -WindowStyle Hidden):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass
#       -File "<repo>\desktop_launchers\CostLog_Win.ps1"

$repoDir = Split-Path -Parent $PSScriptRoot
$Host.UI.RawUI.WindowTitle = 'API Cost Log'
$inv = [Globalization.CultureInfo]::InvariantCulture
# Widen a narrow console (a fresh shortcut's conhost defaults to 80 columns)
# so the full-log lines don't wrap. Best-effort: the cost column stays visible
# even when this fails, because it is placed before the model name.
try {
    $rawUI = $Host.UI.RawUI
    if ($rawUI.BufferSize.Width -lt 100) {
        $bs = $rawUI.BufferSize; $bs.Width = 100; $rawUI.BufferSize = $bs
        $ws = $rawUI.WindowSize
        $ws.Width = [Math]::Min(100, $rawUI.MaxPhysicalWindowSize.Width)
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

try {
    if ($logs.Count -eq 0) {
        Write-Host "No API cost log found."
        Write-Host "  looked in: $share"
        Write-Host "  and:       $repoLog`n"
        Write-Host "MyAgent.py / SelfBot.py append to APICostLog_<machine>.txt when a run"
        Write-Host "ends with priced API usage. (Nothing is logged for Ollama, unmatched"
        Write-Host "model prefixes, or STOPped runs.)"
        return
    }

    # Parse "timestamp;provider;model;cost" from every machine's file, then sort
    # by timestamp -- cross-machine order comes from the field, not file order.
    $rows = foreach ($logf in $logs) {
        foreach ($line in Get-Content -LiteralPath $logf.Path -Encoding UTF8) {
            if ([string]::IsNullOrWhiteSpace($line)) { continue }
            $f = $line.Split(';')
            if ($f.Count -lt 4) { continue }   # also skips the rotation marker line
            [pscustomobject]@{
                Time = $f[0]; Provider = $f[1]; Model = $f[2]
                Cost = [double]::Parse($f[3], $inv); Machine = $logf.Machine
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
        $todaySum = [double]($rows | Where-Object { $_.Time.StartsWith($today) } | Measure-Object Cost -Sum).Sum
        $monthSum = [double]($rows | Where-Object { $_.Time.StartsWith($month) } | Measure-Object Cost -Sum).Sum
        $out += ('  {0} runs - ${1:N4} total' -f $rows.Count, $total)
        $out += ('  span: {0}  ->  {1}' -f $rows[0].Time, $rows[-1].Time)
        $out += ('  today ({0}):      ${1:N4}' -f $today, $todaySum)
        $out += ('  this month ({0}): ${1:N4}' -f $month, $monthSum)
        $out += @('', '  By machine:')
        $out += ($rows | Group-Object Machine |
            Sort-Object { ($_.Group | Measure-Object Cost -Sum).Sum } -Descending |
            ForEach-Object { '    {0,-24} ${1,10:N4}  ({2} runs)' -f $_.Name, ($_.Group | Measure-Object Cost -Sum).Sum, $_.Count })
        $out += @('', '  By provider:')
        $out += ($rows | Group-Object Provider |
            Sort-Object { ($_.Group | Measure-Object Cost -Sum).Sum } -Descending |
            ForEach-Object { '    {0,-12} ${1,10:N4}  ({2} runs)' -f $_.Name, ($_.Group | Measure-Object Cost -Sum).Sum, $_.Count })
        $out += @('', '  By model (highest spend first):')
        $out += ($rows | Group-Object Model |
            Sort-Object { ($_.Group | Measure-Object Cost -Sum).Sum } -Descending |
            ForEach-Object { '    {0,-32} ${1,10:N4}  ({2})' -f $_.Name, ($_.Group | Measure-Object Cost -Sum).Sum, $_.Count })
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
        $machW = 7; $provW = 8
        foreach ($r in $rows) {
            if ($r.Machine.Length -gt $machW) { $machW = $r.Machine.Length }
            if ($r.Provider.Length -gt $provW) { $provW = $r.Provider.Length }
        }
        $fmt = "{0,-19} {1,-$machW} {2,-$provW} {3,9} {4}"
        $out += ($fmt -f 'DATE/TIME', 'MACHINE', 'PROVIDER', 'COST(USD)', 'MODEL')
        $out += ($fmt -f ('-' * 9), ('-' * 7), ('-' * 8), ('-' * 9), ('-' * 5))
        $rev = @($rows); [array]::Reverse($rev)
        $out += @(foreach ($r in $rev) {
            $fmt -f $r.Time, $r.Machine, $r.Provider, ('{0:N4}' -f $r.Cost), $r.Model
        })
    }

    try { $out | Out-Host -Paging } catch { $out | Write-Host }
}
finally {
    Read-Host "`nPress Enter to close"
}
