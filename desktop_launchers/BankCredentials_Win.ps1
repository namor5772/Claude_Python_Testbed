# BankCredentials_Win.ps1 -- Desktop-shortcut console for storing or updating the bank
# login details MyAgent's westpac-login / anz-login / cba-login skills read from Windows
# Credential Manager (2026-11-09; the Windows twin of BankCredentials.applescript /
# store_bank_credentials.command). It shows each bank's state (stored / missing), lets
# the user pick one, and runs that skill's own helper in `store-credentials` mode, which
# asks for the ID and the password twice with no echo and writes the vault. Nothing is
# written to a file and this script never sees a value: the helper reads the keyboard
# itself. A VISIBLE console on purpose -- the helper's getpass needs a real console and
# refuses a redirected stdin -- so the shortcut targets powershell.exe directly, like the
# two log viewers, never conhost --headless:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass
#       -File "<repo>\desktop_launchers\BankCredentials_Win.ps1"
# The repo is resolved from this file's location and the skills folder through
# myagent.datapaths.resolve_skills_dir (the OneDrive share), so any clone works as-is;
# only the .lnk is per-machine. -Status prints the three states and exits (for a quick
# look, and for a check from a script).
param([switch]$Status)

$repoDir = Split-Path -Parent $PSScriptRoot
$python  = Join-Path $repoDir '.venv\Scripts\python.exe'
try { $Host.UI.RawUI.WindowTitle = 'Bank Credentials' } catch {}
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$banks = @(
    @{ Key = '1'; Name = 'Westpac';        Skill = 'westpac-login'; Script = 'windows_login_support.py'; What = 'Customer ID + password' },
    @{ Key = '2'; Name = 'ANZ';            Skill = 'anz-login';     Script = 'anz_login_support.py';     What = 'Customer Registration Number + password' },
    @{ Key = '3'; Name = 'CBA (CommBank)'; Skill = 'cba-login';     Script = 'cba_login_support.py';     What = 'client number + password' }
)

function Resolve-SkillsDir {
    if (-not (Test-Path -LiteralPath $python)) { throw "The venv python is missing: $python" }
    Push-Location $repoDir
    try { $dir = & $python -B -c "from myagent.datapaths import resolve_skills_dir; print(resolve_skills_dir())" 2>$null }
    finally { Pop-Location }
    if (-not $dir) { throw 'Could not resolve the skills folder (myagent.datapaths.resolve_skills_dir).' }
    return ($dir | Select-Object -Last 1).Trim()
}

function Get-BankState($bank, $skillsDir) {
    $helper = Join-Path $skillsDir "$($bank.Skill)\scripts\$($bank.Script)"
    if (-not (Test-Path -LiteralPath $helper)) { return @{ Helper = $helper; Text = 'helper not found' } }
    $raw = & $python -B $helper check-credentials 2>&1 | Out-String
    try { $json = $raw | ConvertFrom-Json } catch { $json = $null }
    $text = switch ($(if ($json) { $json.status } else { '' })) {
        'CREDENTIALS_AVAILABLE' { 'stored' }
        'CREDENTIALS_MISSING'   { 'MISSING' }
        default                 { if ($json -and $json.reason) { "STOP: $($json.reason)" } else { 'no answer from the helper' } }
    }
    return @{ Helper = $helper; Text = $text }
}

function Show-Menu($states) {
    Clear-Host
    Write-Host 'Bank Credentials - store or update the login details MyAgent''s bank skills use'
    Write-Host ''
    Write-Host 'They go into Windows Credential Manager (this Windows user''s own vault). Nothing is'
    Write-Host 'written to a file, nothing you type is shown, and MyAgent reads them only inside the'
    Write-Host 'bank skill''s helper at login time. Do this after changing a password on the bank''s site.'
    Write-Host ''
    foreach ($b in $banks) {
        Write-Host ('  {0}) {1,-15} {2,-42} {3}' -f $b.Key, $b.Name, $b.What, $states[$b.Key].Text)
    }
    Write-Host '  Q) close'
    Write-Host ''
}

try {
    $skillsDir = Resolve-SkillsDir
    $states = @{}
    foreach ($b in $banks) { $states[$b.Key] = Get-BankState $b $skillsDir }

    if ($Status) {
        foreach ($b in $banks) { Write-Host ('{0,-15} {1}' -f $b.Name, $states[$b.Key].Text) }
        exit 0
    }
    if ([Console]::IsInputRedirected) {
        Write-Host 'Bank Credentials needs a real console window: double-click the "Bank Credentials" Desktop shortcut.'
        foreach ($b in $banks) { Write-Host ('  {0,-15} {1}' -f $b.Name, $states[$b.Key].Text) }
        exit 1
    }

    while ($true) {
        Show-Menu $states
        $choice = (Read-Host 'Which bank? [1/2/3/Q]').Trim().ToUpper()
        if ($choice -eq 'Q' -or $choice -eq '') { break }
        $bank = $banks | Where-Object { $_.Key -eq $choice } | Select-Object -First 1
        if (-not $bank) { continue }
        $helper = $states[$bank.Key].Helper
        if (-not (Test-Path -LiteralPath $helper)) {
            Write-Host "`nThe $($bank.Name) helper is missing: $helper"
            Read-Host 'Press Enter to go back'
            continue
        }
        Write-Host ''
        Write-Host "$($bank.Name): the helper will ask for the $($bank.What -replace ' \+ ', ', then the new ') and the password again."
        Write-Host 'Type each answer and press Enter - nothing appears as you type.'
        Write-Host ''
        & $python -B $helper store-credentials
        if ($LASTEXITCODE -eq 0) {
            Write-Host "`nStored. MyAgent's next $($bank.Name) login uses the new details."
        } else {
            Write-Host "`nNothing stored (see the reason above). Run it again when ready."
        }
        $states[$bank.Key] = Get-BankState $bank $skillsDir
        Read-Host "`nPress Enter to go back to the menu"
    }
}
catch {
    Write-Host "Bank Credentials could not start: $($_.Exception.Message)"
    try { Read-Host "`nPress Enter to close" } catch {}
    exit 1
}
