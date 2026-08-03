# CostLog_Win.ps1 -- Desktop-shortcut viewer for the MyAgent/SelfBot API cost
# log (Windows twin of CostLog.applescript / view_costlog.command). Shows a
# spend summary first (grand total, today, this month, by machine, by provider,
# by model), then the cost per session (consecutive runs on one machine no more
# than 30 min apart count as one working session), then every run
# most-recent-first. Since 2026-08-03 each machine
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

        # Cost per session: the log has no session id (MyAgent appends one line
        # per run, SelfBot one per process close), so a "session" is
        # reconstructed by time adjacency -- consecutive runs logged by the
        # SAME machine no more than $sessionGapMin apart (a scheduled morning
        # batch, an orchestrator plus its waited children, a SelfBot duo's two
        # lines) group into one session. Machine-keyed because the merged rows
        # interleave machines: a Mac run minutes after a Windows run is
        # parallel work, not the same session.
        $sessionGapMin = 30
        $sessions = @()
        $openSession = @{}
        foreach ($r in $rows) {
            $t = $null
            try { $t = [datetime]::ParseExact($r.Time, 'yyyy-MM-dd HH:mm:ss', $inv) } catch {}
            $s = $openSession[$r.Machine]
            if ($null -eq $s -or $null -eq $t -or $null -eq $s.End -or
                    ($t - $s.End).TotalMinutes -gt $sessionGapMin) {
                $s = @{ Start = $r.Time; End = $t; EndStr = $r.Time; Machine = $r.Machine
                        Runs = 0; Cost = 0.0; Models = @() }
                $sessions += $s
                $openSession[$r.Machine] = $s
            }
            $s.Runs++; $s.Cost += $r.Cost; $s.End = $t; $s.EndStr = $r.Time
            if ($s.Models -notcontains $r.Model) { $s.Models += $r.Model }
        }
        $out += @('', ('=' * 21 + ' SESSIONS (most recent first) ' + '=' * 21), '')
        $out += ('  {0} sessions - consecutive runs on one machine <= {1} min apart' -f $sessions.Count, $sessionGapMin)
        $out += ''
        $revSessions = @($sessions); [array]::Reverse($revSessions)
        foreach ($s in $revSessions) {
            $models = $s.Models -join ', '
            if ($models.Length -gt 42) { $models = $models.Substring(0, 41) + '~' }
            $startDisp = if ($s.Start.Length -ge 16) { $s.Start.Substring(0, 16) } else { $s.Start }
            $endDisp = if ($s.EndStr.Length -ge 19) { $s.EndStr.Substring(11, 5) } else { $s.EndStr }
            $runWord = if ($s.Runs -eq 1) { 'run' } else { 'runs' }
            $out += ('  {0} -> {1}  {2,-24} ${3,10:N4}  ({4} {5}; {6})' -f $startDisp, $endDisp, $s.Machine, $s.Cost, $s.Runs, $runWord, $models)
        }
    }
    $out += @('', ('=' * 21 + ' FULL LOG (most recent first) ' + '=' * 21), '')

    if ($rows.Count -gt 0) {
        $rev = @($rows); [array]::Reverse($rev)
        $table = $rev | Format-Table -AutoSize `
            @{ Label = 'DATE/TIME'; Expression = { $_.Time } },
            @{ Label = 'MACHINE';   Expression = { $_.Machine } },
            @{ Label = 'PROVIDER';  Expression = { $_.Provider } },
            @{ Label = 'MODEL';     Expression = { $_.Model } },
            @{ Label = 'COST(USD)'; Expression = { '{0:N4}' -f $_.Cost } } | Out-String -Stream
        $out += $table
    }

    try { $out | Out-Host -Paging } catch { $out | Write-Host }
}
finally {
    Read-Host "`nPress Enter to close"
}
