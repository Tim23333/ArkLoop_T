# Build ArkLoop standalone exe (PyWebview + React).
# Usage: powershell -ExecutionPolicy Bypass -File build_arkloop.ps1
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw ".venv not found at $py" }

# 1. Build frontend
Write-Host '[1/3] Building frontend (ui)...' -ForegroundColor Cyan
Push-Location (Join-Path $root 'ui')
if (-not (Test-Path 'node_modules')) { npm install }
npm run build
Pop-Location
if (-not (Test-Path (Join-Path $root 'ui\dist\index.html'))) {
    throw 'ui/dist/index.html missing after npm run build'
}

# 2. Ensure icon.ico exists (PyInstaller only accepts .ico on Windows)
Write-Host '[2/3] Checking icon.ico...' -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $root 'icon.ico'))) {
    throw 'icon.ico not found at project root; place a Windows .ico icon file there.'
}

# 3. Run PyInstaller
Write-Host '[3/3] Running PyInstaller...' -ForegroundColor Cyan
& $py -m PyInstaller arkloop.spec --clean --noconfirm

$out = Join-Path $root 'dist\ArkLoop\ArkLoop.exe'
if (-not (Test-Path $out)) {
    throw 'Build finished but ArkLoop.exe not found.'
}

# Seed user-writable files next to the EXE so first launch has defaults.
$distDir = Split-Path -Parent $out
$cfgDst = Join-Path $distDir 'config.json'
if (-not (Test-Path $cfgDst)) {
    Copy-Item (Join-Path $root 'config.example.json') $cfgDst
}
New-Item -ItemType Directory -Force -Path (Join-Path $distDir 'timelines') | Out-Null

Write-Host "Build OK: $out" -ForegroundColor Green
