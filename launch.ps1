# CalaVoiceStudio launcher (fallback path when no packaged exe exists yet).
# Prefers the bundled exe; otherwise finds a Python interpreter that can import PySide6.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here

$exeCandidates = @(
    (Join-Path $here 'CalaVoiceStudio.exe'),
    (Join-Path $here 'dist\CalaVoiceStudio\CalaVoiceStudio.exe')
)
foreach ($exe in $exeCandidates) {
    if (Test-Path -LiteralPath $exe) {
        Start-Process -FilePath $exe -ArgumentList $args -WorkingDirectory $here
        exit 0
    }
}

$candidates = New-Object System.Collections.Generic.List[string]
foreach ($name in @('python', 'python3')) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source) { $candidates.Add($cmd.Source) }
}
$globRoots = @($env:LOCALAPPDATA + '\Python', $env:LOCALAPPDATA + '\Programs\Python', 'C:\Python315', 'C:\Python314', 'C:\Python313', 'C:\Python312')
foreach ($root in $globRoots) {
    if (Test-Path -LiteralPath $root) {
        Get-ChildItem -LiteralPath $root -Filter 'python.exe' -Recurse -Depth 2 -ErrorAction SilentlyContinue |
            ForEach-Object { $candidates.Add($_.FullName) }
    }
}

foreach ($exe in ($candidates | Select-Object -Unique)) {
    try {
        & $exe -c "import PySide6" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Using interpreter: $exe"
            & $exe (Join-Path $here 'main.py') @args
            exit $LASTEXITCODE
        }
    } catch { }
}

Write-Host '[ERROR] No packaged exe and no Python with PySide6 found.'
Write-Host 'Install it with:  python -m pip install PySide6'
exit 1
