$udd=$null
foreach ($p in (Get-CimInstance Win32_Process -Filter "Name='chrome.exe'")) {
  if ($p.CommandLine -match '--user-data-dir="([^"]+)"') { $udd=$Matches[1]; break }
  elseif ($p.CommandLine -match '--user-data-dir=(\S+)') { $udd=$Matches[1]; break }
}
if (-not $udd) { $udd = "$env:TEMP\myagent_browser_debug" }
Get-Process chrome -ErrorAction SilentlyContinue | ForEach-Object { $_.CloseMainWindow() | Out-Null }
Start-Sleep -Seconds 4
if (Get-Process chrome -ErrorAction SilentlyContinue) { Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue; Start-Sleep -Seconds 2 }
$prefs = Join-Path $udd "Default\Preferences"
if (Test-Path $prefs) { (Get-Content $prefs -Raw) -replace '"exit_type"\s*:\s*"[^"]*"','"exit_type":"Normal"' | Set-Content $prefs -Encoding UTF8 -NoNewline }
Get-Process chrome -ErrorAction SilentlyContinue
