param(
    [string]$DistPath = "",
    [string]$WorkPath = ""
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Missing virtualenv Python: $python"
}

if (-not $DistPath) {
    $DistPath = Join-Path $root "dist"
}
if (-not $WorkPath) {
    $WorkPath = Join-Path $root "build"
}

New-Item -ItemType Directory -Path $DistPath -Force | Out-Null
New-Item -ItemType Directory -Path $WorkPath -Force | Out-Null

& $python -m pip install -r requirements-build.txt
& $python -m PyInstaller --noconfirm --clean --distpath $DistPath --workpath $WorkPath YTSubViewer.spec

$portableRoot = Join-Path $DistPath "YTSubViewer"
Get-ChildItem -Path $portableRoot -Filter *.cmd -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
$launcher = Join-Path $portableRoot "Launch YTSubViewer.cmd"
@(
    "@echo off"
    "setlocal"
    "start """" ""%~dp0YTSubViewer.exe"""
) | Set-Content -Path $launcher -Encoding ASCII

Write-Host ""
Write-Host "Portable build ready:" -ForegroundColor Green
Write-Host $portableRoot -ForegroundColor Green
