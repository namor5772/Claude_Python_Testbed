# Position two SelfBot windows side by side and focus instance 1
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class Win32 {
    [DllImport("user32.dll")]
    public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
    [DllImport("user32.dll")]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}
'@

Add-Type -AssemblyName System.Windows.Forms
$workArea = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$halfWidth = [int]($workArea.Width / 2)

# Read instance 1's PID from the lock file
$lockFile = Join-Path $PSScriptRoot "selfbot.lock"
$inst1Pid = 0
if (Test-Path $lockFile) {
    $inst1Pid = [int](Get-Content $lockFile -Raw).Trim()
}

$allWindows = @(Get-Process | Where-Object { $_.MainWindowTitle -like '*Claude SelfBot*' } | Select-Object -First 2)

if ($allWindows.Count -ge 2 -and $inst1Pid -gt 0) {
    # Identify instance 1 and 2 by lock file PID
    $inst1 = $allWindows | Where-Object { $_.Id -eq $inst1Pid }
    $inst2 = $allWindows | Where-Object { $_.Id -ne $inst1Pid }

    if ($inst1 -and $inst2) {
        # Position instance 2 (right) FIRST so it doesn't steal focus last
        [Win32]::MoveWindow($inst2.MainWindowHandle, $workArea.Left + $halfWidth, $workArea.Top, $halfWidth, $workArea.Height, $true) | Out-Null
        # Position instance 1 (left) LAST
        [Win32]::MoveWindow($inst1.MainWindowHandle, $workArea.Left, $workArea.Top, $halfWidth, $workArea.Height, $true) | Out-Null
        Start-Sleep -Milliseconds 300
        # Simulate Alt press to bypass Windows foreground restrictions, then force focus
        [Win32]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
        [Win32]::ShowWindow($inst1.MainWindowHandle, 5) | Out-Null
        [Win32]::SetForegroundWindow($inst1.MainWindowHandle) | Out-Null
        [Win32]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
    }
}
