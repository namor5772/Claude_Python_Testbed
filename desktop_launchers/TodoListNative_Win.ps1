# TodoListNative_Win.ps1 -- Desktop-shortcut launcher for TodoList.exe, the
# native C++/Win32 port of TodoList.py (Windows twin of
# TodoListNative_launcher.applescript; build the exe with
# .\build_todolist_native.ps1 -- it is gitignored and per-machine).
#
# Launch-or-focus across BOTH implementations, and for the same reason as the
# AppleScript: native and Python poll the same OneDrive-synced todos.json
# every 5 seconds, so a second instance -- native or Python -- would race the
# first's mtime poll and Overwrite/Reload dialogs. A press therefore focuses
# a running TodoList.exe, or failing that a running TodoList.py, before it
# ever launches anything. Shows a "build it first" dialog if the exe is
# missing. Unlike the macOS launcher there is no dotfile sourcing: Windows
# env vars (an optional TODOLIST_DATA_DIR override included) are system-wide
# and inherited by GUI processes. The repo is resolved from this file's own
# location, so any clone works unedited; only the .lnk shortcut is
# per-machine.
#
# The desktop shortcut targets:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden
#       -File "<repo>\desktop_launchers\TodoListNative_Win.ps1"

$repoDir = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $repoDir 'TodoList.exe'

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class TodoNativeWin {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr h, int n);
}
"@

function Focus-Window([IntPtr]$handle) {
    [void][TodoNativeWin]::ShowWindowAsync($handle, 9)  # SW_RESTORE
    [void][TodoNativeWin]::SetForegroundWindow($handle)
}

# A native instance already running? Focus it.
$native = Get-Process TodoList -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero } | Select-Object -First 1
if ($native) {
    Focus-Window $native.MainWindowHandle
    return
}

# A TodoList.py instance? Focus that instead of racing its sync poll. The
# venv pythonw is a stub that re-execs base python, so match on either image
# name and pick whichever process actually owns the Tk window.
$pyRunning = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*TodoList.py*' })
foreach ($p in $pyRunning) {
    $proc = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
    if ($proc -and $proc.MainWindowHandle -ne [IntPtr]::Zero) {
        Focus-Window $proc.MainWindowHandle
        return
    }
}
if ($pyRunning.Count -gt 0) { return }  # running but no window mapped yet -- no duplicate

# Not running: launch the exe, or explain how to build it.
if (-not (Test-Path $exe)) {
    Add-Type -AssemblyName System.Windows.Forms
    [void][System.Windows.Forms.MessageBox]::Show(
        "TodoList.exe has not been built on this machine yet.`n`n" +
        "Run  .\build_todolist_native.ps1  in the repo first.",
        'TodoList (Native)',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Warning)
    return
}
Start-Process -FilePath $exe -WorkingDirectory $repoDir
