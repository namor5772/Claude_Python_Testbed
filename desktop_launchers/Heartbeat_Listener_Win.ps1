# Heartbeat_Listener_Win.ps1 — keep Heartbeat's resident Telegram listener running.
#
# Heartbeat.py --listen is the process that lets a Telegram message to this
# machine's bot start a MyAgent run and that posts the run's result back to
# the chat the moment it ends (README, "Zero-token automation"). It replaced
# the every-few-minutes scheduled pass on 2026-10-26: one long-polling process
# picks a message up within a second instead of at the next tick.
#
# Registered as the Task Scheduler job "MyAgent_Heartbeat_Listener" by the
# block below, run once with -Register (then it starts the task at once):
#   powershell -ExecutionPolicy Bypass -File Heartbeat_Listener_Win.ps1 -Register
#
# The task runs the venv's pythonw.exe — a windowless interpreter, so no
# console ever appears and no conhost trick is needed (unlike the PowerShell
# jobs) — at logon, with no time limit, allowed on battery, and started again
# by a second trigger every 10 minutes whenever it is not running (since
# 2026-11-05: the task's restart-after-a-minute setting retries a task that
# Task Scheduler could not START, never a process that exited by itself, so a
# listener that stood down at logon was gone until the next logon). The
# listener's own lock file keeps it to one instance, so a start racing a live
# listener is harmless: the second one logs "already running" and exits.
#
# Without -Register the script starts the listener directly (hidden), which is
# what the task's action could have been; the task calls pythonw.exe itself so
# nothing sits between Task Scheduler and the process it restarts.
param(
    [switch]$Register
)
$TaskName = "MyAgent_Heartbeat_Listener"
$repo = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repo ".venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
    Write-Output "venv interpreter not found: $pythonw"
    exit 1
}
if ($Register) {
    $action = New-ScheduledTaskAction -Execute $pythonw -Argument "Heartbeat.py --listen" `
        -WorkingDirectory $repo
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    # Every 10 minutes from midnight, indefinitely (no -RepetitionDuration:
    # [TimeSpan]::MaxValue is "out of range" on this Windows 11 build). With
    # MultipleInstances IgnoreNew the firing is ignored while the listener
    # runs and starts it when it is gone — the restart that the RestartCount
    # setting below is not: that one retries a task that failed to START.
    $every10 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
        -RepetitionInterval (New-TimeSpan -Minutes 10)
    # ExecutionTimeLimit 0 = no limit (the task's "stop if it runs longer
    # than" box unticked); RestartCount / RestartInterval retry a start that
    # failed (an interpreter that could not be launched), kept for that case.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($logon, $every10) `
        -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "Registered scheduled task '$TaskName' (at logon and every 10 min while not running, no time limit) and started it."
    return
}
Start-Process -FilePath $pythonw -ArgumentList "Heartbeat.py --listen" -WorkingDirectory $repo
Write-Output "Started Heartbeat.py --listen via $pythonw"
