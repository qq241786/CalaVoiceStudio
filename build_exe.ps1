# CalaVoiceStudio / PyInstaller build (repeatable, ASCII-only on purpose:
# PowerShell 5.1 reads BOM-less .ps1 as ANSI, so non-ASCII text breaks parsing).
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = $env:CALAVOICE_PYTHON
if (-not $python) {
    # Auto-detect a Python that can import PySide6 (no hard-coded local paths).
    # Native stderr must not become a terminating error here, so relax EAP temporarily.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $candidates = New-Object System.Collections.Generic.List[string]
    foreach ($name in @('python', 'python3')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) { $candidates.Add($cmd.Source) }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @('-3.14', '-3.13', '-3.12', '-3')) {
            $exeOut = & py $v -c "import sys;print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $exeOut) { $candidates.Add(($exeOut | Select-Object -Last 1)) }
        }
    }
    foreach ($root in @("$env:LOCALAPPDATA\Python", "$env:LOCALAPPDATA\Programs\Python", 'C:\')) {
        if (Test-Path -LiteralPath $root) {
            Get-ChildItem -LiteralPath $root -Filter 'python.exe' -Recurse -Depth 3 -ErrorAction SilentlyContinue |
                ForEach-Object { $candidates.Add($_.FullName) }
        }
    }
    foreach ($cand in ($candidates | Select-Object -Unique)) {
        & $cand -c "import PySide6" *> $null
        if ($LASTEXITCODE -eq 0) { $python = $cand; break }
    }
    $ErrorActionPreference = $prevEap
}
if (-not (Test-Path $python)) { throw "Python interpreter not found: $python (set CALAVOICE_PYTHON)" }
$exe = Join-Path $root 'dist\CalaVoiceStudio\CalaVoiceStudio.exe'
Push-Location $root
try {
    & $python -m PyInstaller --noconfirm --clean --distpath (Join-Path $root 'dist') --workpath (Join-Path $root 'build') (Join-Path $root 'build.spec')
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
if (-not (Test-Path $exe)) { throw "Build finished but exe missing: $exe" }
Write-Host "OK -> $exe"
