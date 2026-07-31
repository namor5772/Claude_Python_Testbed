<#
.SYNOPSIS
  Close ONLY MyAgent's automation browser, leaving personal windows untouched.

.DESCRIPTION
  Browser-automation instructions call this via run_command to shut the browser
  down cleanly at the end of a run.

  Every process it touches is selected by matching the automation profile path
  on the process command line, so a personal Chrome/Edge window open at the same
  time is never closed. MyAgent launches its browser with
  --user-data-dir=<temp>\myagent_browser_debug (myagent/browser_mixin.py), and
  that path is this script's default target.

  Sequence: ask the matched windows to close, poll for them to exit, force-kill
  only the matched leftovers, then reset "exit_type" to "Normal" in the
  profile's Preferences file. That last step matters because Chrome writes
  exit_type "Crashed" when force-killed, which puts a "Restore pages?" bar on
  top of the page the NEXT automation run needs to click.

.PARAMETER UserDataDir
  The browser profile to close. Defaults to MyAgent's automation profile.

.PARAMETER GraceSeconds
  How long to wait for a graceful close before force-killing. Default 8.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File close_chrome.ps1
#>
[CmdletBinding()]
param(
    [string]$UserDataDir = (Join-Path $env:TEMP "myagent_browser_debug"),
    [int]$GraceSeconds = 8
)

$ErrorActionPreference = "Stop"

# browser_mixin.py picks Chrome first, then Edge, on Windows.
$ProcFilter = "Name='chrome.exe' OR Name='msedge.exe'"

function Get-AutomationProcs {
    param([string]$Dir)
    # Selecting by profile path is what keeps personal windows safe. Renderer
    # and GPU children inherit --user-data-dir too, so they match as well —
    # which is wanted: they are all part of the automation instance.
    $escaped = [regex]::Escape($Dir)
    $all = @(Get-CimInstance Win32_Process -Filter $ProcFilter -ErrorAction SilentlyContinue)
    $hits = @($all | Where-Object { $_.CommandLine -and $_.CommandLine -match $escaped })
    if ($hits.Count -eq 0) {
        # TEMP can surface as an 8.3 short path (RUNNER~1) or a differently
        # expanded form on the command line. Fall back to the profile's leaf
        # folder name, which is distinctive enough to stay safe.
        $leaf = [regex]::Escape((Split-Path $Dir -Leaf))
        $hits = @($all | Where-Object { $_.CommandLine -and $_.CommandLine -match $leaf })
    }
    return $hits
}

function Get-ProcIds {
    param($Procs)
    return @($Procs | ForEach-Object { $_.ProcessId } | Sort-Object -Unique)
}

$procs = Get-AutomationProcs -Dir $UserDataDir
if ($procs.Count -eq 0) {
    Write-Output "No automation browser running for profile: $UserDataDir"
    Write-Output "Nothing closed; any personal browser windows are untouched."
    exit 0
}

$targetIds = Get-ProcIds -Procs $procs
Write-Output ("Automation browser processes: " + ($targetIds -join ", "))

# Graceful close. Only the top-level browser process owns a window; the child
# processes exit on their own once it goes — which is exactly why each call is
# wrapped: closing the parent kills the children mid-loop, so by the time this
# reaches a child PID it has usually already exited and both MainWindowHandle
# and CloseMainWindow() throw "Process has exited". Under
# $ErrorActionPreference = "Stop" that benign race would abort the script
# before the exit_type reset below — the one step that actually has to happen.
foreach ($procId in $targetIds) {
    try {
        $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
        if ($p -and -not $p.HasExited -and $p.MainWindowHandle -ne 0) {
            $p.CloseMainWindow() | Out-Null
        }
    } catch {
        # Already gone, or no window to close. Either way there is nothing to
        # do and the force-kill pass below is the backstop.
    }
}

# Poll rather than sleeping a flat interval: usually done in well under a second.
$deadline = (Get-Date).AddSeconds($GraceSeconds)
while ((Get-Date) -lt $deadline) {
    if ((Get-AutomationProcs -Dir $UserDataDir).Count -eq 0) { break }
    Start-Sleep -Milliseconds 500
}

# Force-kill ONLY our own leftovers, never a blanket Stop-Process -Name chrome.
$left = Get-AutomationProcs -Dir $UserDataDir
if ($left.Count -gt 0) {
    $leftIds = Get-ProcIds -Procs $left
    Write-Output ("Graceful close timed out; force-killing: " + ($leftIds -join ", "))
    foreach ($procId in $leftIds) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
}

# Clear the crash marker so the next run does not get a "Restore pages?" bar.
$prefs = Join-Path $UserDataDir "Default\Preferences"
if (Test-Path $prefs) {
    $content = (Get-Content $prefs -Raw) -replace '"exit_type"\s*:\s*"[^"]*"', '"exit_type":"Normal"'
    # NOT Set-Content -Encoding UTF8: in Windows PowerShell 5.1 that writes a
    # UTF-8 BOM, and a BOM ahead of the JSON makes Chrome discard Preferences
    # as corrupt and reset the profile — which would silently undo this fix.
    [System.IO.File]::WriteAllText($prefs, $content, (New-Object System.Text.UTF8Encoding($false)))
    Write-Output "Reset exit_type to Normal in: $prefs"
} else {
    Write-Output "No Preferences file at $prefs (nothing to reset)."
}

$still = Get-AutomationProcs -Dir $UserDataDir
if ($still.Count -gt 0) {
    Write-Output "WARNING: automation browser processes survived:"
    Write-Output (($still | Select-Object ProcessId, Name | Format-Table -AutoSize | Out-String).Trim())
    exit 1
}

Write-Output "Automation browser closed; personal browser windows untouched."
exit 0
