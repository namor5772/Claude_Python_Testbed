# CSVEditor_Win.ps1 -- Desktop-shortcut launcher for CSVEditor.py (Windows twin
# of CSVEditor_launcher.applescript). Launches the editor with the venv pythonw
# (no console window); if an instance is already running it brings that window
# to the front instead of starting a second copy -- the same launch-or-focus
# behaviour as the AppleScript. The repo is resolved from this file's own
# location, so any clone works unedited; only the .lnk shortcut is per-machine.
#
# The desktop shortcut targets a headless conhost, NOT powershell.exe
# -WindowStyle Hidden: powershell.exe is a console program whose window Windows
# creates before that switch is parsed, so a bare powershell target flashed a
# console on every click (measured and fixed 2026-09-03):
#   conhost.exe --headless powershell.exe -NoProfile -ExecutionPolicy Bypass
#       -File "<repo>\desktop_launchers\CSVEditor_Win.ps1"

$repoDir = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $repoDir '.venv\Scripts\pythonw.exe'
$script  = Join-Path $repoDir 'CSVEditor.py'

# Already running? Focus its window rather than starting a second instance.
# The venv pythonw is a stub that re-execs base python, so match on either image
# name and pick whichever process actually owns the Tk window.
$running = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*CSVEditor.py*' })

if ($running.Count -gt 0) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class CsvWin {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h, int n);
}
"@
    foreach ($p in $running) {
        $proc = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
        if ($proc -and $proc.MainWindowHandle -ne [IntPtr]::Zero) {
            [void][CsvWin]::ShowWindowAsync($proc.MainWindowHandle, 9)  # SW_RESTORE
            [void][CsvWin]::SetForegroundWindow($proc.MainWindowHandle)
            return
        }
    }
    return  # running but the window isn't mapped yet -- don't start a duplicate
}

# Not running: launch detached. Fall back to a system pythonw if the venv
# hasn't been built on this machine yet.
if (-not (Test-Path $pythonw)) { $pythonw = 'pythonw.exe' }
Start-Process -FilePath $pythonw -ArgumentList @("`"$script`"") -WorkingDirectory $repoDir -WindowStyle Hidden
