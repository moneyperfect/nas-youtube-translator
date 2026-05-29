$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$releaseBuildRoot = Join-Path $root ".release-build"
$portableDist = Join-Path $releaseBuildRoot "dist"
$portableWork = Join-Path $releaseBuildRoot "work"

& (Join-Path $root "build_portable.ps1") -DistPath $portableDist -WorkPath $portableWork

$candidates = @(
    "D:\Tools\InnoSetup6\ISCC.exe",
    "D:\InnoSetup6\ISCC.exe",
    "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)
$iscc = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    throw "Missing Inno Setup 6 ISCC.exe. Please install Inno Setup 6 first."
}

$env:YTSUBVIEWER_BUILD_ROOT = (Join-Path $portableDist "YTSubViewer")
& $iscc (Join-Path $root "installer\YTSubViewer.iss")

Write-Host ""
Write-Host "Installer build ready:" -ForegroundColor Green
Write-Host (Join-Path $root "release") -ForegroundColor Green
