# ProtonBridge_Watchdog_Win.ps1 — start Proton Bridge if it is not running.
#
# Proton Bridge is the localhost IMAP/SMTP server (127.0.0.1:1143 / :1025)
# that MyAgent's proton_* tools and UnreadSummary.py talk to. Bridge's own
# "start on login" toggle only drops a Startup-folder shortcut, so a Bridge
# that was quit from the tray, or that crashed, stays dead until the next
# login and every digest pass logs
#   ACCOUNT ERROR romangroblicki@proton.me: ConnectionRefusedError ...
#
# Registered as the Task Scheduler job "ProtonBridge_Watchdog" (at logon +
# every 15 minutes) by the block at the bottom, run once with -Register:
#   powershell -ExecutionPolicy Bypass -File ProtonBridge_Watchdog_Win.ps1 -Register
#
# The task's action is "conhost.exe --headless powershell.exe ...", NOT
# "powershell.exe -WindowStyle Hidden": powershell.exe is a console program,
# so Windows creates its console (a Windows Terminal window when Terminal is
# the default host) before PowerShell ever parses that switch, and an
# interactive-session task flashed a Terminal window on every 15-minute run
# (measured 2026-09-03: ~0.5 s each, and under Terminal the switch never hid
# it at all). A headless conhost hands the script a pseudoconsole that has no
# window, so nothing appears.
#
# Idempotent: bridge.exe is the headless server the GUI launcher spawns; if
# it is alive there is nothing to do. The launcher itself refuses a second
# instance (bridge-v3.lock), so a spurious start is harmless too.

param(
    [switch]$Register,
    [string]$Launcher = "C:\Program Files\Proton AG\Proton Mail Bridge\proton-bridge.exe"
)

$TaskName = "ProtonBridge_Watchdog"

if ($Register) {
    $scriptPath = $MyInvocation.MyCommand.Path
    # Headless conhost = no console window at all (see the header comment).
    $conhost = Join-Path $env:SystemRoot "System32\conhost.exe"
    $action  = New-ScheduledTaskAction -Execute $conhost `
        -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
    $logon   = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $logon.Delay = "PT2M"      # let the Startup-folder shortcut go first
    $repeat  = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
        -RepetitionInterval (New-TimeSpan -Minutes 15)
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action `
        -Trigger @($logon, $repeat) -Settings $settings -Force | Out-Null
    Write-Output "Registered scheduled task '$TaskName' (at logon +2 min, then every 15 min)."
    return
}

if (Get-Process -Name "bridge" -ErrorAction SilentlyContinue) {
    exit 0
}
if (-not (Test-Path $Launcher)) {
    Write-Output "Bridge launcher not found: $Launcher"
    exit 1
}
Start-Process -FilePath $Launcher -ArgumentList "--no-window"
Write-Output "Started Proton Bridge via $Launcher"
