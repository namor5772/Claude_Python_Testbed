# build_basicform.ps1 - assemble and link BasicForm.exe, the x64 MASM Win32
# "basic form" demo (BasicForm.asm + BasicForm.rc + BasicForm.manifest).
# Windows-only - there is no macOS twin, the source is x64 MASM against
# user32 / comctl32. Needs Visual Studio (any edition) with the "Desktop
# development with C++" workload: ml64 / rc / link are located via vswhere,
# no PATH setup required.
#
#   .\build_basicform.ps1             # build .\BasicForm.exe
#   .\build_basicform.ps1 -Shortcut   # build, then create / refresh the Desktop shortcut
#   .\build_basicform.ps1 -Run        # build, then launch it
#
# BasicForm.exe is gitignored and per-machine, like TodoList.exe. A running
# instance holds the exe open, so the build closes one first (the app has no
# data to lose). The Desktop shortcut points straight at the exe: a
# /SUBSYSTEM:WINDOWS program has no console, so unlike the Python launchers in
# desktop_launchers/ it needs no headless-conhost wrapper. The exe carries its
# own icon (BasicForm.rc embeds desktop_launchers\icon_basicform.ico as icon
# 1), so Explorer, the taskbar and Alt-Tab show it too; the shortcut names the
# .ico explicitly so it keeps its icon while the exe is being rebuilt.

param([switch]$Shortcut, [switch]$Run)
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$exe = Join-Path $repo 'BasicForm.exe'

# -- locate MSVC (vswhere ships with every VS/Build Tools install) --
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vswhere)) {
    throw 'vswhere.exe not found - install Visual Studio (or Build Tools) with the C++ workload'
}
$vsroot = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsroot) { throw 'no Visual Studio installation with the C++ toolset found' }
$vcvars = Join-Path $vsroot 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { throw "vcvars64.bat not found under $vsroot" }

$tmp = Join-Path $env:TEMP ('basicform_build_' + [IO.Path]::GetRandomFileName().Replace('.', ''))
New-Item -ItemType Directory -Path $tmp | Out-Null

# Run one tool inside the vcvars64 environment (ml64/rc/link are not on PATH outside it)
function Invoke-VC([string]$CmdLine, [string]$Dir = $repo) {
    cmd /s /c " `"$vcvars`" >nul 2>&1 && cd /d `"$Dir`" && $CmdLine"
    if ($LASTEXITCODE -ne 0) { throw "command failed (exit $LASTEXITCODE): $CmdLine" }
}

$running = @(Get-Process BasicForm -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) {
    Write-Host "-- closing $($running.Count) running BasicForm instance(s)"
    $running | Stop-Process -Force
    Start-Sleep -Milliseconds 300
}

try {
    Write-Host '-- assembling BasicForm.asm'
    # ml64 writes the .obj into the current directory, so run it from $tmp
    Invoke-VC "ml64 /nologo /c /W3 `"$repo\BasicForm.asm`"" $tmp

    Write-Host '-- compiling resources (icon + manifest)'
    Invoke-VC "rc /nologo /fo `"$tmp\BasicForm.res`" BasicForm.rc"

    Write-Host '-- linking BasicForm.exe'
    # /MANIFEST:NO - the manifest is already inside BasicForm.res; the linker's
    # default would ALSO write a BasicForm.exe.manifest file next to the exe
    Invoke-VC ("link /nologo /SUBSYSTEM:WINDOWS /ENTRY:start /MANIFEST:NO /OUT:`"$exe`" " +
               "`"$tmp\BasicForm.obj`" `"$tmp\BasicForm.res`" user32.lib kernel32.lib gdi32.lib comctl32.lib")
    Write-Host "built $exe"
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

if ($Shortcut) {
    # [Environment]::GetFolderPath('Desktop') - the Desktop may be OneDrive-backed
    $lnkPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Basic Form.lnk'
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($lnkPath)
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = $repo
    $lnk.IconLocation = "$repo\desktop_launchers\icon_basicform.ico,0"
    $lnk.Description = 'Basic Form (x64 MASM) - a Win32 window written in assembler'
    $lnk.Save()
    Write-Host "shortcut: $lnkPath"
}

if ($Run) {
    Start-Process -FilePath $exe -WorkingDirectory $repo
    Write-Host 'launched BasicForm.exe'
}
