# HeartbeatLog_Win.ps1 -- Desktop-shortcut viewer for Heartbeat.py's log (Windows
# twin of HeartbeatLog.applescript / view_heartbeat.command). Shows the meaningful
# events first (idle "nothing found" ticks hidden), then the full log, paged in the
# console. On Windows the log lives at the REPO ROOT (Heartbeat.py: LOG_FILE =
# BASE_DIR / "heartbeat.log"), not under a Logs folder like macOS. The repo is
# resolved from this file's own location, so any clone works as-is -- only the .lnk
# is per-machine.
#
# The desktop shortcut targets a VISIBLE window (it's a viewer, NOT -WindowStyle Hidden):
#   powershell.exe -NoProfile -ExecutionPolicy Bypass
#       -File "<repo>\desktop_launchers\HeartbeatLog_Win.ps1"

$repoDir = Split-Path -Parent $PSScriptRoot
$log     = Join-Path $repoDir 'heartbeat.log'
$Host.UI.RawUI.WindowTitle = 'Heartbeat Log'
# Heartbeat.py writes the log as UTF-8 (open(..., encoding="utf-8")); Windows
# PowerShell 5.1 would otherwise read it as ANSI and mojibake the em-dashes in
# "checked - nothing found". Render the console as UTF-8 too (guarded: throws
# when there is no real console, e.g. a redirected/headless invocation).
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

try {
    if (-not (Test-Path $log)) {
        Write-Host "Heartbeat log not found:`n  $log`n"
        Write-Host "Heartbeat.py creates it on its first scheduled tick (it polls every 5 min)."
        return
    }

    $lines  = @(Get-Content -LiteralPath $log -Encoding UTF8)
    $events = @($lines | Where-Object { $_ -notmatch 'nothing found' })

    $out  = @("Heartbeat log - $log",
              "$($lines.Count) ticks logged - $($events.Count) meaningful events shown below (idle ticks hidden)",
              ('=' * 76), '')
    $out += $events
    $out += @('', ('=' * 22 + ' FULL LOG (every tick, newest at the end) ' + '=' * 22), '')
    $out += $lines

    try { $out | Out-Host -Paging } catch { $out | Write-Host }
}
finally {
    Read-Host "`nPress Enter to close"
}
