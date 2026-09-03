# UnreadSummary_Win.ps1 -- Desktop-shortcut launcher for UnreadSummary.py
# (Windows twin of UnreadSummary.applescript). Runs the venv python hidden,
# then mirrors the Mac feedback: success -> chime + self-dismissing dialog
# showing the run's log line; failure -> blocking error dialog with the log
# tail. On Windows the script logs to <repo>\unread_summary.log.
#
# The desktop shortcut targets a headless conhost, NOT powershell.exe
# -WindowStyle Hidden: powershell.exe is a console program whose window Windows
# creates before that switch is parsed, so a bare powershell target flashed a
# console on every click (measured and fixed 2026-09-03):
#   conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass
#       -File "<repo>\desktop_launchers\UnreadSummary_Win.ps1"
# Unlike the AppleScript (absolute repo path patched in by rebuild.sh), the
# repo is resolved from this file's own location, so any clone works as-is;
# only the .lnk is per-machine. -DryRun passes --dry-run through (read-only
# pass, nothing sent or marked) for testing the plumbing end to end.
param([switch]$DryRun)

$repoDir = Split-Path -Parent $PSScriptRoot
$python  = Join-Path $repoDir '.venv\Scripts\python.exe'
$logFile = Join-Path $repoDir 'unread_summary.log'

$pyArgs = @('UnreadSummary.py')
if ($DryRun) { $pyArgs += '--dry-run' }

$proc = Start-Process -FilePath $python -ArgumentList $pyArgs `
    -WorkingDirectory $repoDir -WindowStyle Hidden -PassThru -Wait

$shell = New-Object -ComObject WScript.Shell
if ($proc.ExitCode -eq 0) {
    $line = ''
    try {
        $line = [string](Get-Content $logFile -Tail 1 -Encoding UTF8)
        if ($line.Length -gt 20) { $line = $line.Substring(20) }  # strip "YYYY-MM-DD HH:MM:SS "
    } catch {}
    [System.Media.SystemSounds]::Asterisk.Play()
    $null = $shell.Popup("Run complete:`n$line", 6, 'UnreadSummary', 64)
} else {
    $tail = ''
    try { $tail = (Get-Content $logFile -Tail 3 -Encoding UTF8) -join "`n" } catch {}
    $null = $shell.Popup("UnreadSummary failed (exit $($proc.ExitCode)).`n`nLog:`n$tail", 0, 'UnreadSummary', 16)
}
