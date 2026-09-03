# SelfBot_Win.ps1 -- Desktop-shortcut launcher for SelfBot.py (Windows twin of
# SelfBot_launcher.applescript). Launches a NEW SelfBot instance each time with the
# venv pythonw (no console window). SelfBot is a two-instance app by design -- the
# whole point is that a second instance can chat with the first (self-chat) -- so,
# like MyAgent_Win.ps1 and unlike CSVEditor_Win.ps1, there is deliberately no
# launch-or-focus: a launch-or-focus shortcut could never open the second window.
# SelfBot.py itself cascades a manually-opened second instance down-right so the two
# windows don't stack on the same saved geometry. The repo is resolved from this
# file's own location, so any clone works unedited; only the .lnk is per-machine.
#
# This launches solo instances (no --no-geometry). Double-click twice to get the
# two self-chatting windows; for the auto-positioned side-by-side duo layout with
# name/focus wiring, use LaunchSelfBot.bat at the repo root instead.
#
# The desktop shortcut targets a headless conhost, NOT powershell.exe
# -WindowStyle Hidden: powershell.exe is a console program whose window Windows
# creates before that switch is parsed, so a bare powershell target flashed a
# console on every click (measured and fixed 2026-09-03):
#   conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass
#       -File "<repo>\desktop_launchers\SelfBot_Win.ps1"

$repoDir = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repoDir '.venv\Scripts\pythonw.exe'
$script  = Join-Path $repoDir 'SelfBot.py'

# Fall back to a system pythonw if the venv hasn't been built on this machine yet.
if (-not (Test-Path $pythonw)) { $pythonw = 'pythonw.exe' }
Start-Process -FilePath $pythonw -ArgumentList @("`"$script`"") -WorkingDirectory $repoDir -WindowStyle Hidden
