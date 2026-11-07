<#
.SYNOPSIS
Make every MyAgent skill a Claude Code skill on Windows — link_skills.sh's twin.

.DESCRIPTION
One DIRECTORY JUNCTION per skill folder, directly under ~\.claude\skills,
each pointing into the OneDrive skills tree. A junction needs no privilege
(a symbolic link needs Developer Mode or an elevated shell, and Git Bash's
`ln -s` silently copies without them), and to every file API it is the
folder itself, so Claude Code's loader — which does not scan a nested
folder, probed on the Mac 2026-11-07 — sees each skill at the top level.

Idempotent: adds a junction for a new skill, removes a junction of ours
whose skill is gone, touches nothing else in the folder. Re-run after a
skill is added or deleted. -Remove drops every junction of ours.

SAFETY: a junction is removed with [IO.Directory]::Delete(link), which
unlinks the reparse point only. NEVER `Remove-Item -Recurse` a junction —
Windows PowerShell 5.1 follows it and deletes the TARGET's contents, which
here is the shared skills tree.

.PARAMETER Remove
Remove every junction this script made and stop.

.EXAMPLE
.\claude_code_mcp\link_skills.ps1
#>
[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = "Stop"

function Resolve-SkillsDir {
    if ($env:MYAGENT_SKILLS_DIR) { return $env:MYAGENT_SKILLS_DIR }
    foreach ($v in @($env:OneDrive, $env:OneDriveConsumer, $env:OneDriveCommercial)) {
        if ($v) {
            $p = Join-Path $v "MyAppShare\skills"
            if (Test-Path -LiteralPath $p) { return $p }
        }
    }
    throw "No OneDrive MyAppShare\skills folder found; set MYAGENT_SKILLS_DIR."
}

function Get-JunctionTarget([System.IO.FileSystemInfo]$item) {
    # A reparse point's target; PowerShell 5.1 exposes it as a string array.
    if (-not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $null }
    $t = $item.Target
    if ($t -is [array]) { $t = $t[0] }
    if ($t) { return [string]$t } else { return $null }
}

function Remove-Junction([string]$path) {
    # Unlinks the reparse point only; the target is untouched.
    [System.IO.Directory]::Delete($path)
}

$skillsDir = (Resolve-Path -LiteralPath (Resolve-SkillsDir)).Path.TrimEnd('\')
$claudeSkills = Join-Path $HOME ".claude\skills"
if (-not (Test-Path -LiteralPath $claudeSkills)) {
    New-Item -ItemType Directory -Path $claudeSkills | Out-Null
}

$added = 0; $removed = 0; $kept = 0

# Junctions of ours: a reparse point under ~\.claude\skills whose target is
# inside the skills tree. Stale when that skill's SKILL.md is gone (or when
# -Remove was asked).
foreach ($item in Get-ChildItem -LiteralPath $claudeSkills -Force) {
    $target = Get-JunctionTarget $item
    if (-not $target) { continue }
    if (-not $target.StartsWith($skillsDir + '\', [StringComparison]::OrdinalIgnoreCase)) { continue }
    if ($Remove -or -not (Test-Path -LiteralPath (Join-Path $target "SKILL.md"))) {
        Remove-Junction $item.FullName
        $removed++
    }
}
if ($Remove) {
    Write-Output "MyAgent skill junctions removed from ${claudeSkills}: $removed"
    exit 0
}

foreach ($dir in Get-ChildItem -LiteralPath $skillsDir -Directory) {
    if (-not (Test-Path -LiteralPath (Join-Path $dir.FullName "SKILL.md"))) { continue }
    $link = Join-Path $claudeSkills $dir.Name
    if (Test-Path -LiteralPath $link) {
        $existing = Get-Item -LiteralPath $link -Force
        $target = Get-JunctionTarget $existing
        if ($target -and ($target.TrimEnd('\') -ieq $dir.FullName.TrimEnd('\'))) { $kept++; continue }
        if (-not $target) {
            Write-Warning "skip $($dir.Name): a real entry of that name exists in $claudeSkills"
            continue
        }
        Remove-Junction $link    # ours, pointing elsewhere (a moved tree): re-point it
    }
    New-Item -ItemType Junction -Path $link -Target $dir.FullName | Out-Null
    $added++
}

Write-Output "MyAgent skills linked into ${claudeSkills}: $added added, $removed removed, $kept unchanged"
