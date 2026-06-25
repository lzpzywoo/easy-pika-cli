# Build Windows release zip for PikPak Download
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$Version = (py -3 -c "from pikpak_downloader import __version__; print(__version__)").Trim()
$OutName = "PikPakDownload-v$Version-windows-x64"
$DistDir = Join-Path $Root "dist\$OutName"
$ZipPath = Join-Path $Root "dist\$OutName.zip"

Write-Host "==> PikPak Download release build v$Version"

Write-Host "==> Installing build dependencies..."
py -3 -m pip install -r requirements.txt pyinstaller --quiet

Write-Host "==> Running PyInstaller..."
if (Test-Path (Join-Path $Root "build\pikpak_gui")) {
    Remove-Item -Recurse -Force (Join-Path $Root "build\pikpak_gui")
}
py -3 -m PyInstaller build\pikpak_gui.spec --noconfirm --distpath dist --workpath build\pikpak_gui

$BuiltDir = Join-Path $Root "dist\PikPakDownload"
if (-not (Test-Path $BuiltDir)) {
    throw "Build failed: $BuiltDir not found"
}

Write-Host "==> Assembling release folder..."
if (Test-Path $DistDir) { Remove-Item -Recurse -Force $DistDir }
New-Item -ItemType Directory -Path $DistDir | Out-Null
Copy-Item -Path "$BuiltDir\*" -Destination $DistDir -Recurse
Copy-Item -Path "build\release_notes.txt" -Destination (Join-Path $DistDir "使用说明.txt")
Copy-Item -Path "README.md" -Destination $DistDir

@'
@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "" "PikPakDownload.exe"
'@ | Set-Content -Path (Join-Path $DistDir "启动 PikPak Download.bat") -Encoding UTF8

Write-Host "==> Creating zip..."
if (Test-Path $ZipPath) { Remove-Item -Force $ZipPath }
Compress-Archive -Path $DistDir -DestinationPath $ZipPath -Force

$SizeMB = [math]::Round((Get-Item $ZipPath).Length / 1MB, 1)
Write-Host ""
Write-Host "Done."
Write-Host "  Folder: $DistDir"
Write-Host "  Zip:    $ZipPath ($SizeMB MB)"
