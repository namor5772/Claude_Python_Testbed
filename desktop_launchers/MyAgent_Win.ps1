# MyAgent_Win.ps1 -- Desktop-shortcut launcher for MyAgent.py (Windows twin of
# MyAgent_launcher.applescript). Launches a NEW MyAgent instance each time with
# the venv pythonw (no console window). MyAgent is multi-instance by design --
# each instance claims the lowest free lock number -- so, unlike CSVEditor_Win.ps1,
# there is deliberately no launch-or-focus. The repo is resolved from this file's
# own location, so any clone works unedited; only the .lnk shortcut is per-machine.
#
# The desktop shortcut targets:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden
#       -File "<repo>\desktop_launchers\MyAgent_Win.ps1"

$repoDir = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repoDir '.venv\Scripts\pythonw.exe'
$script  = Join-Path $repoDir 'MyAgent.py'

# Fall back to a system pythonw if the venv hasn't been built on this machine yet.
if (-not (Test-Path $pythonw)) { $pythonw = 'pythonw.exe' }
Start-Process -FilePath $pythonw -ArgumentList @("`"$script`"") -WorkingDirectory $repoDir -WindowStyle Hidden
