# CostLog_Win.ps1 -- Desktop-shortcut viewer for MyAgent's API cost log (Windows
# twin of CostLog.applescript / view_costlog.command). Shows a spend summary first
# (grand total, today, this month, by provider, by model), then every run
# most-recent-first. APICostLog.txt is at the REPO ROOT on both OSes; the repo is
# resolved from this file's own location, so any clone works as-is -- only the .lnk
# is per-machine. Each line is "timestamp;provider;model;cost" (semicolon-delimited).
#
# The desktop shortcut targets a VISIBLE window (it's a viewer, NOT -WindowStyle Hidden):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass
#       -File "<repo>\desktop_launchers\CostLog_Win.ps1"

$repoDir = Split-Path -Parent $PSScriptRoot
$log     = Join-Path $repoDir 'APICostLog.txt'
$Host.UI.RawUI.WindowTitle = 'API Cost Log'
$inv = [Globalization.CultureInfo]::InvariantCulture
# MyAgent writes the log as UTF-8; read and render it as UTF-8 so a non-ASCII
# provider/model name can't mojibake (guarded: the console-encoding setter
# throws when there is no real console, e.g. a redirected/headless invocation).
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

try {
    if (-not (Test-Path $log)) {
        Write-Host "API cost log not found:`n  $log`n"
        Write-Host "MyAgent.py appends to it when a run ends with priced API usage."
        Write-Host "(Nothing is logged for Ollama, unmatched model prefixes, or STOPped runs.)"
        return
    }

    # Parse "timestamp;provider;model;cost", skipping blank/malformed lines.
    $rows = foreach ($line in Get-Content -LiteralPath $log -Encoding UTF8) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $f = $line.Split(';')
        if ($f.Count -lt 4) { continue }
        [pscustomobject]@{
            Time = $f[0]; Provider = $f[1]; Model = $f[2]
            Cost = [double]::Parse($f[3], $inv)
        }
    }
    $rows = @($rows)

    $out = @("API Cost Log - $log", ('=' * 76), '', 'SUMMARY')
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
        $rev = @($rows); [array]::Reverse($rev)
        $table = $rev | Format-Table -AutoSize `
            @{ Label = 'DATE/TIME'; Expression = { $_.Time } },
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
