param([string]$PythonVersion = '3.12')
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    & py "-$PythonVersion" -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
}
& ./.venv/Scripts/python.exe -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw 'RiftLift dependency installation failed' }
$env:RIFTLIFT_HOME = Join-Path $PSScriptRoot 'portable'
& ./.venv/Scripts/python.exe -m riftlift.cli setup
if ($LASTEXITCODE -ne 0) { throw 'Native runtime installation failed' }
& ./.venv/Scripts/python.exe -m riftlift.cli doctor --no-paste
if ($LASTEXITCODE -eq 2) {
    Write-Host 'Runtime installed. Configure the headset/OpenXR runtime before game testing.'
} elseif ($LASTEXITCODE -ne 0) {
    throw 'Windows diagnostics failed'
}

} finally {
    Pop-Location
}
exit 0
