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
# jobs) — at logon, with no time limit, allowed on battery, and restarted a
# minute after any exit for as long as it keeps exiting. The listener's own
# lock file keeps it to one instance, so a restart racing a live listener is
# harmless: the second one logs "already running" and exits.
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
    # ExecutionTimeLimit 0 = no limit (the task's "stop if it runs longer
    # than" box unticked); RestartCount / RestartInterval bring it back after
    # a crash, a kill, or a sleep/wake that the process did not survive.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
        -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $logon `
        -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Write-Output "Registered scheduled task '$TaskName' (at logon, no time limit, restart after 1 min) and started it."
    return
}
Start-Process -FilePath $pythonw -ArgumentList "Heartbeat.py --listen" -WorkingDirectory $repo
Write-Output "Started Heartbeat.py --listen via $pythonw"
