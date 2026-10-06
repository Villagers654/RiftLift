param(
    [Parameter(Mandatory = $true)][string]$RuntimeDirectory,
    [string]$Python = 'python',
    [string]$Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    [switch]$AppOnly
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$runtime = (Resolve-Path -LiteralPath $RuntimeDirectory).Path
$env:RIFTLIFT_PACKAGE_RUNTIME = $runtime
$env:RIFTLIFT_PACKAGE_ICON = Join-Path $root 'build\windows\riftlift.ico'
$originalPath = $env:PATH
$pythonExe = (Get-Command $Python -ErrorAction Stop).Source
# DLL discovery must not pull same-named libraries from unrelated developer tools.
$env:PATH = "$(Split-Path $pythonExe);$env:WINDIR\System32;$env:WINDIR"
Push-Location $root
try {
    & $Python scripts/prepare-windows-package.py $runtime $env:RIFTLIFT_PACKAGE_ICON
    if ($LASTEXITCODE -ne 0) { throw 'Windows package preparation failed' }
    & $Python -m PyInstaller --noconfirm --clean --distpath dist/windows --workpath build/windows/pyinstaller scripts/windows.spec
    if ($LASTEXITCODE -ne 0) { throw 'Windows application build failed' }
    if ($AppOnly) { return }
    $version = & $Python -c 'from riftlift import __version__; print(__version__)'
    if ($LASTEXITCODE -ne 0) { throw 'Could not read RiftLift version' }
    $bundle = Join-Path $root 'dist\windows\RiftLift'
    & $Iscc "/DAppVersion=$version" "/DBundleDir=$bundle" "/O$root\dist\windows" scripts/windows-installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Windows installer build failed' }
    $installer = Join-Path $root "dist\windows\RiftLift-Setup-$version-x64.exe"
    $hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $(Split-Path $installer -Leaf)" | Set-Content -Encoding ascii "$installer.sha256"
    Write-Host "Installer ready: $installer"
} finally {
    $env:PATH = $originalPath
    Pop-Location
}
