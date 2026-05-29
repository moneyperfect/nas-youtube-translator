$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
$version = & $python -c "import sys; sys.path.insert(0, 'src'); from ytsubviewer.config import APP_VERSION; print(APP_VERSION)"
$releaseRoot = Join-Path $root "release\$version"
$portableRoot = Join-Path $releaseRoot "portable"
$portableDist = Join-Path $portableRoot "dist"
$portableWork = Join-Path $portableRoot "work"
$portableZip = Join-Path $releaseRoot "YTSubViewer-$version-win-x64-portable.zip"

New-Item -ItemType Directory -Path $releaseRoot -Force | Out-Null

& (Join-Path $root "build_portable.ps1") -DistPath $portableDist -WorkPath $portableWork

if (Test-Path $portableZip) {
    Remove-Item -Path $portableZip -Force
}
Compress-Archive -Path (Join-Path $portableDist "YTSubViewer\*") -DestinationPath $portableZip -Force

$env:YTSUBVIEWER_BUILD_ROOT = Join-Path $portableDist "YTSubViewer"
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
& $iscc (Join-Path $root "installer\YTSubViewer.iss")

@(
    "YTSubViewer $version"
    ""
    "Included:"
    "- Windows installer"
    "- Windows portable zip"
    "- Local license flow and user-supplied DeepSeek API key"
    ""
    "Recommended delivery:"
    "1. Send the installer to paying users."
    "2. Keep the portable zip for beta testers and emergency fallback."
    "3. Tell users to enter their DeepSeek API key on first launch."
) | Set-Content -Path (Join-Path $releaseRoot "RELEASE_NOTES.txt") -Encoding UTF8

@(
    "YTSubViewer $version license admin notes"
    ""
    "1. Packaged builds no longer accept DEV-LICENSE."
    "2. Before generating paid activation codes, set:"
    "   YTSUBVIEWER_LICENSE_SECRET=<your-private-secret>"
    "3. Generate a code with:"
    "   .venv\Scripts\python.exe scripts\generate_license.py --secret <your-private-secret> --licensee <customer-name> --plan standard --days 365"
    "4. Never send the private secret to end users."
) | Set-Content -Path (Join-Path $releaseRoot "LICENSE_ADMIN_README.txt") -Encoding UTF8

Write-Host ""
Write-Host "Release build ready:" -ForegroundColor Green
Write-Host $releaseRoot -ForegroundColor Green
