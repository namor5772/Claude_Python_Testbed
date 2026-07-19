# build_todolist_native.ps1 - build TodoList.exe (the native C++/Win32 port of
# TodoList.py) and run its test suite first. Windows twin of
# build_todolist_native.sh; needs Visual Studio (any edition) with the
# "Desktop development with C++" workload - located via vswhere, no PATH setup
# required.
#
#   .\build_todolist_native.ps1              # test + build .\TodoList.exe
#   .\build_todolist_native.ps1 -SkipTests   # build only
#
# The interop stage proves the implementations round-trip the same todos.json:
# OneDrive syncs ONE file between machines that may run TodoList.py, the macOS
# TodoList.exe, or this port, so the JSON each writes must be readable by the
# others. On Windows the check is stricter than the macOS one - the native
# rewrite must be BYTE-identical to Python's json.dump(indent=2) output
# (TodoList.cpp reproduces the format, CRLF and ensure_ascii escapes included).
# Python is taken from the repo venv when present.

param([switch]$SkipTests)
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot

# -- locate MSVC (vswhere ships with every VS/Build Tools install) --
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vswhere)) {
    throw 'vswhere.exe not found - install Visual Studio (or Build Tools) with the C++ workload'
}
$vsroot = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsroot) { throw 'no Visual Studio installation with the C++ toolset found' }
$vcvars = Join-Path $vsroot 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { throw "vcvars64.bat not found under $vsroot" }

$tmp = Join-Path $env:TEMP ('todolist_native_build_' + [IO.Path]::GetRandomFileName().Replace('.', ''))
New-Item -ItemType Directory -Path $tmp | Out-Null

# Run one tool inside the vcvars64 environment (cl/rc are not on PATH outside it)
function Invoke-VC([string]$CmdLine) {
    cmd /s /c " `"$vcvars`" >nul 2>&1 && cd /d `"$repo`" && $CmdLine"
    if ($LASTEXITCODE -ne 0) { throw "command failed (exit $LASTEXITCODE): $CmdLine" }
}

$cxx = '/nologo /std:c++17 /EHsc /W4 /O2 /utf-8 /DUNICODE /D_UNICODE'

try {
    if (-not $SkipTests) {
        Write-Host '-- compiling tests'
        Invoke-VC "cl $cxx /Fo`"$tmp\\`" tests\test_todolist_native.cpp /Fe`"$tmp\todolist_tests.exe`""

        Write-Host '-- running unit tests'
        & "$tmp\todolist_tests.exe"
        if ($LASTEXITCODE -ne 0) { throw 'unit tests failed' }

        $py = Join-Path $repo '.venv\Scripts\python.exe'
        if (-not (Test-Path $py)) { $py = 'py' }
        Write-Host '-- python <-> native JSON interop'
        # Python writes (json.dump indent=2, ensure_ascii escapes, text-mode
        # CRLF) -> native reads and rewrites -> Python verifies the data AND
        # the exact bytes survived unchanged.
        @'
import json, sys
data = {"todos": [
    {"text": "unicode caf\u00e9 \u2713 \u2014 em dash", "priority": "High",
     "category": "General", "due": "22/07/2026", "done": False, "created": "19/07/2026"},
    {"text": "plain", "priority": "Low", "category": "Errands", "due": "",
     "done": True, "created": "01/01/2026", "future_key": [1, {"nested": None}]},
], "categories": ["General", "Errands"]}
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)
'@ | Set-Content -Path "$tmp\interop_write.py" -Encoding Ascii
        & $py "$tmp\interop_write.py" "$tmp\py.json"
        if ($LASTEXITCODE -ne 0) { throw 'python interop write failed' }
        & "$tmp\todolist_tests.exe" roundtrip "$tmp\py.json" "$tmp\native.json"
        if ($LASTEXITCODE -ne 0) { throw 'native roundtrip failed' }
        @'
import json, sys
a = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open(sys.argv[2], encoding="utf-8"))
assert a == b, "interop mismatch:\n%r\n%r" % (a, b)
raw_a = open(sys.argv[1], "rb").read()
raw_b = open(sys.argv[2], "rb").read()
assert raw_a == raw_b, "interop data equal but bytes differ:\n%r\n%r" % (raw_a, raw_b)
print("interop OK: python-written and native-rewritten todos.json are byte-identical")
'@ | Set-Content -Path "$tmp\interop_check.py" -Encoding Ascii
        & $py "$tmp\interop_check.py" "$tmp\py.json" "$tmp\native.json"
        if ($LASTEXITCODE -ne 0) { throw 'python interop check failed' }
    }

    Write-Host '-- building TodoList.exe'
    Invoke-VC "rc /nologo /fo `"$tmp\TodoList.res`" TodoList.rc"
    Invoke-VC "cl $cxx /Fo`"$tmp\\`" TodoList.cpp `"$tmp\TodoList.res`" /Fe`"$repo\TodoList.exe`" /link /SUBSYSTEM:WINDOWS"
    Write-Host "built $repo\TodoList.exe"
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}
