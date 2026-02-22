# Focus instance 1's window after both SelfBot instances are launched
# Positioning is handled by SelfBot.py itself via --no-geometry flag
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}
'@

# Read instance 1's PID from the lock file
$lockFile = Join-Path $PSScriptRoot "selfbot.lock"
$inst1Pid = 0
if (Test-Path $lockFile) {
    $inst1Pid = [int](Get-Content $lockFile -Raw).Trim()
}

# Wait for instance 1's window to appear (up to 20 seconds)
$inst1 = $null
for ($i = 0; $i -lt 40; $i++) {
    $inst1 = Get-Process | Where-Object { $_.Id -eq $inst1Pid -and $_.MainWindowHandle -ne [IntPtr]::Zero } | Select-Object -First 1
    if ($inst1) { break }
    Start-Sleep -Milliseconds 500
}

if ($inst1) {
    # Simulate Alt press to bypass Windows foreground restrictions, then force focus
    [Win32]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
    [Win32]::ShowWindow($inst1.MainWindowHandle, 5) | Out-Null
    [Win32]::SetForegroundWindow($inst1.MainWindowHandle) | Out-Null
    [Win32]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
}
