# Build ArkLoop standalone exe (PyWebview + Tesseract).
# Usage: powershell -ExecutionPolicy Bypass -File build_arkloop.ps1
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw ".venv not found at $py" }

# 1. Build frontend
Write-Host '[1/4] Building frontend (ui)...' -ForegroundColor Cyan
Push-Location (Join-Path $root 'ui')
if (-not (Test-Path 'node_modules')) { npm install }
npm run build
Pop-Location
if (-not (Test-Path (Join-Path $root 'ui\dist\index.html'))) {
    throw 'ui/dist/index.html missing after npm run build'
}

# 2. Ensure icon.ico exists (PyInstaller only accepts .ico on Windows)
Write-Host '[2/4] Checking icon.ico...' -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $root 'icon.ico'))) {
    throw 'icon.ico not found at project root; place a Windows .ico icon file there.'
}

# 3. Stage Tesseract-OCR at project root (OPTIONAL).
#    tesserocr has no Python 3.12 wheel and the live pipeline degrades
#    gracefully without it (pause-detection OCR only), so Tesseract is no
#    longer required to build.  The arkloop.spec bundles it only if present.
Write-Host '[3/4] Staging Tesseract-OCR (optional)...' -ForegroundColor Cyan
$tessDst = Join-Path $root 'Tesseract-OCR'
if (-not (Test-Path $tessDst)) {
    $tessSrc = Join-Path $root '_internal\Tesseract-OCR'
    if (Test-Path $tessSrc) {
        Copy-Item -Recurse -Force $tessSrc $tessDst
    } else {
        Write-Host "Tesseract-OCR not found; building WITHOUT it (pause-detection OCR will be unavailable). To enable, place a Tesseract folder (tesseract.exe + tessdata/) at project root." -ForegroundColor Yellow
    }
}

# 4. Run PyInstaller
Write-Host '[4/4] Running PyInstaller...' -ForegroundColor Cyan
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
