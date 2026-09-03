param([string]$Installer)
$ErrorActionPreference = 'Stop'
if ($env:CI -ne 'true') { throw 'Run installer lifecycle tests on an ephemeral CI worker' }
$root = Split-Path $PSScriptRoot -Parent
if (-not $Installer) {
    $Installer = (Get-ChildItem "$root\dist\windows\RiftLift-Setup-*.exe" | Select-Object -First 1).FullName
}
if (-not $Installer) { throw 'Build the Windows installer first' }
$evidence = Join-Path $root 'build\install-check'
$appDir = Join-Path $evidence 'App with spaces'
$env:RIFTLIFT_HOME = Join-Path $evidence 'User data'
$env:QT_QPA_PLATFORM = 'offscreen'
New-Item -ItemType Directory -Force $evidence | Out-Null
function Run-Bounded([string]$File, [string[]]$Arguments) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(60000)) {
        Stop-Process -Id $process.Id
        throw "Timed out: $File"
    }
    if ($process.ExitCode -ne 0) { throw "$File exited $($process.ExitCode)" }
}
# Use only an isolated installation on an ephemeral CI worker.
Run-Bounded $Installer @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=', ('/DIR="' + $appDir + '"'), ('/LOG="' + $evidence + '\install.log"'))
try {
    $exe = Join-Path $appDir 'RiftLift.exe'
    $env:PATH = "$env:WINDIR\System32;$env:WINDIR"
    Run-Bounded $exe @('--package-check', ('"' + $evidence + '\first-launch"'))
    $result = Get-Content "$evidence\first-launch\result.json" | ConvertFrom-Json
    if (-not $result.success -or -not $result.icon_loaded) { throw 'Installed app check failed' }
    $shortcutPath = Join-Path ([Environment]::GetFolderPath('Programs')) 'RiftLift.lnk'
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
    if ($shortcut.TargetPath -ne $exe) { throw 'Start menu shortcut points to the wrong executable' }
    $marker = Join-Path $env:RIFTLIFT_HOME 'preserve-on-upgrade.txt'
    Set-Content $marker 'preserve games and settings'
    Run-Bounded $Installer @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/DIR="' + $appDir + '"'), ('/LOG="' + $evidence + '\upgrade.log"'))
    if ((Get-Content $marker) -ne 'preserve games and settings') { throw 'Upgrade changed user data' }
    Run-Bounded $exe @('--package-check', ('"' + $evidence + '\after-upgrade"'))
} finally {
    if (Test-Path "$appDir\unins000.exe") {
        Run-Bounded "$appDir\unins000.exe" @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', ('/LOG="' + $evidence + '\uninstall.log"'))
    }
}
if (Test-Path "$appDir\RiftLift.exe") { throw 'Uninstall left the application executable' }
if (Test-Path $shortcutPath) { throw 'Uninstall left the Start menu shortcut' }
if ((Get-Content $marker) -ne 'preserve games and settings') { throw 'Uninstall changed user data' }
Write-Host 'Install, upgrade, launch, shortcut and uninstall checks passed.'
