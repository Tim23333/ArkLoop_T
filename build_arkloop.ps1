# Build ArkLoop standalone exe (PyWebview + React).
# Usage: powershell -ExecutionPolicy Bypass -File build_arkloop.ps1
param(
    [switch]$BuildInstaller
)

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# Keep all build caches and temporary files inside the project drive.
$buildCache = Join-Path $root '.build-cache'
$buildTemp = Join-Path $buildCache 'temp'
$packageRoot = Join-Path $buildCache 'package'
$packageDist = Join-Path $packageRoot 'dist'
$mainWork = Join-Path $buildCache 'work\arkloop'
$installerWork = Join-Path $buildCache 'work\dependency-installer'
$env:PYINSTALLER_CONFIG_DIR = Join-Path $buildCache 'pyinstaller'
$env:TEMP = $buildTemp
$env:TMP = $buildTemp
New-Item -ItemType Directory -Force -Path $buildTemp | Out-Null
New-Item -ItemType Directory -Force -Path $env:PYINSTALLER_CONFIG_DIR | Out-Null

$cacheFull = [System.IO.Path]::GetFullPath($buildCache).TrimEnd('\') + '\'
$packageFull = [System.IO.Path]::GetFullPath($packageRoot)
if (-not $packageFull.StartsWith($cacheFull, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean package staging path outside .build-cache: $packageFull"
}
if (Test-Path -LiteralPath $packageRoot) {
    Remove-Item -LiteralPath $packageRoot -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $packageDist | Out-Null

$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw ".venv not found at $py" }
$totalSteps = if ($BuildInstaller) { 4 } else { 3 }

# 1. Build frontend
Write-Host "[1/$totalSteps] Building frontend (ui)..." -ForegroundColor Cyan
Push-Location (Join-Path $root 'ui')
if (-not (Test-Path 'node_modules')) { npm install }
npm run build
Pop-Location
if (-not (Test-Path (Join-Path $root 'ui\dist\index.html'))) {
    throw 'ui/dist/index.html missing after npm run build'
}

# 2. Ensure icon.ico exists (PyInstaller only accepts .ico on Windows)
Write-Host "[2/$totalSteps] Checking icon.ico..." -ForegroundColor Cyan
if (-not (Test-Path (Join-Path $root 'icon.ico'))) {
    throw 'icon.ico not found at project root; place a Windows .ico icon file there.'
}

# 3. Run PyInstaller
$mainStep = if ($BuildInstaller) { '[3/4]' } else { '[3/3]' }
Write-Host "$mainStep Building CPU-only ArkLoop..." -ForegroundColor Cyan
& $py -m PyInstaller arkloop.spec --clean --noconfirm --distpath $packageDist --workpath $mainWork
if ($LASTEXITCODE -ne 0) {
    throw "ArkLoop PyInstaller build failed with exit code $LASTEXITCODE."
}

$stagedDist = Join-Path $packageDist 'ArkLoop'
$stagedOut = Join-Path $stagedDist 'ArkLoop.exe'
if (-not (Test-Path $stagedOut)) {
    throw 'Build finished but ArkLoop.exe not found.'
}

if ($BuildInstaller) {
    # The installer contains pip, but never Torch itself. Build it only for an
    # installer release; ordinary app rebuilds leave the existing EXE intact.
    Write-Host '[4/4] Building optional dependency installer...' -ForegroundColor Cyan
    & $py -m PyInstaller dependency_installer.spec --clean --noconfirm --distpath $packageDist --workpath $installerWork
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installer build failed with exit code $LASTEXITCODE."
    }
    $installerBuild = Join-Path $packageDist 'ArkLoopDependencyInstaller.exe'
    if (-not (Test-Path $installerBuild)) {
        throw 'Build finished but ArkLoopDependencyInstaller.exe not found.'
    }
    $stagedInstaller = Join-Path $stagedDist 'ArkLoopDependencyInstaller.exe'
    Copy-Item -LiteralPath $installerBuild -Destination $stagedInstaller -Force
    Remove-Item -LiteralPath $installerBuild -Force
}

# Deploy the staged package without deleting user-writable state. Replacing
# _internal removes stale bundled files while dependencies/, timelines/,
# config.json and logs/ survive upgrades.
$distDir = Join-Path $root 'dist\ArkLoop'
$runningPackageProcesses = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ExecutablePath -and
        [System.IO.Path]::GetFullPath((Split-Path -Parent $_.ExecutablePath)).TrimEnd('\') -eq
            [System.IO.Path]::GetFullPath($distDir).TrimEnd('\')
    }
)
if ($runningPackageProcesses.Count -gt 0) {
    $names = ($runningPackageProcesses | ForEach-Object { "$($_.Name) (PID $($_.ProcessId))" }) -join ', '
    throw "Close ArkLoop package processes before deployment: $names. Staged package: $stagedDist"
}

New-Item -ItemType Directory -Force -Path $distDir | Out-Null
$finalInternal = Join-Path $distDir '_internal'
if (Test-Path -LiteralPath $finalInternal) {
    Remove-Item -LiteralPath $finalInternal -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $stagedDist '_internal') -Destination $finalInternal -Recurse -Force
Get-ChildItem -LiteralPath $stagedDist -File | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $distDir $_.Name) -Force
}

# Seed user-writable files next to the EXE so first launch has defaults.
$cfgDst = Join-Path $distDir 'config.json'
if (-not (Test-Path $cfgDst)) {
    Copy-Item (Join-Path $root 'config.example.json') $cfgDst
}
New-Item -ItemType Directory -Force -Path (Join-Path $distDir 'timelines') | Out-Null
Get-ChildItem -LiteralPath (Join-Path $stagedDist 'timelines') -File -ErrorAction SilentlyContinue | ForEach-Object {
    $timelineOut = Join-Path (Join-Path $distDir 'timelines') $_.Name
    if (-not (Test-Path -LiteralPath $timelineOut)) {
        Copy-Item -LiteralPath $_.FullName -Destination $timelineOut
    }
}

# Every freshly built main app starts in CPU mode. Keep the already-installed
# GPU files so the user can switch back to them from the main page instantly.
$dependencyDir = Join-Path $distDir 'dependencies'
New-Item -ItemType Directory -Force -Path $dependencyDir | Out-Null
$modePayload = [ordered]@{
    mode = 'cpu'
    updated_at = [DateTime]::UtcNow.ToString('o')
    source = 'build'
} | ConvertTo-Json
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
    (Join-Path $dependencyDir 'mode.json'),
    $modePayload + [Environment]::NewLine,
    $utf8NoBom
)

$out = Join-Path $distDir 'ArkLoop.exe'
$installerOut = Join-Path $distDir 'ArkLoopDependencyInstaller.exe'
Write-Host "Build OK: $out" -ForegroundColor Green
Write-Host "Runtime mode reset to CPU; GPU dependency files were preserved." -ForegroundColor Green
if (Test-Path -LiteralPath $installerOut) {
    Write-Host "Dependency installer: $installerOut" -ForegroundColor Green
}
