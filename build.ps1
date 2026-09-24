# iTunes Controller Build & Launch script for Windows PowerShell
[CmdletBinding()]
param(
    [switch]$Run,
    [switch]$Install,
    [switch]$ForceReinstall
)

$ErrorActionPreference = "Stop"
$ScriptDir = if ($PSScriptRoot) { 
    $PSScriptRoot 
} elseif ($MyInvocation.MyCommand.Path) { 
    Split-Path -Parent $MyInvocation.MyCommand.Path 
} else { 
    (Get-Location).Path 
}
Set-Location $ScriptDir

$VenvDir = Join-Path $ScriptDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"
$VenvPyInstaller = Join-Path $VenvDir "Scripts\pyinstaller.exe"

# 1. Locate uv or Python
$UvPath = $null
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $UvPath = (Get-Command uv).Source
} elseif (Test-Path "$env:USERPROFILE\scoop\apps\uv\current\uv.exe") {
    $UvPath = "$env:USERPROFILE\scoop\apps\uv\current\uv.exe"
} elseif (Test-Path "$env:USERPROFILE\scoop\shims\uv.exe") {
    $UvPath = "$env:USERPROFILE\scoop\shims\uv.exe"
}

# 2. Create venv if needed
if (-not (Test-Path $VenvPython)) {
    Write-Host "[itt1] Creating virtual environment (.venv)..." -ForegroundColor Cyan
    if ($UvPath) {
        Write-Host "[itt1] Using uv ($UvPath)..." -ForegroundColor Gray
        & $UvPath venv $VenvDir
    } else {
        $PythonCmd = if (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { "python" }
        Write-Host "[itt1] Using $PythonCmd..." -ForegroundColor Gray
        & $PythonCmd -m venv $VenvDir
    }
}

# 3. Install requirements + pyinstaller
$BuildInstalledMarker = Join-Path $VenvDir ".build_installed"
if ($ForceReinstall -or (-not (Test-Path $BuildInstalledMarker)) -or (-not (Test-Path $VenvPyInstaller))) {
    Write-Host "[itt1] Installing dependencies and PyInstaller..." -ForegroundColor Cyan
    if ($UvPath) {
        & $UvPath pip install --python $VenvPython -r "$ScriptDir\requirements.txt" -r "$ScriptDir\requirements-windows.txt" pyinstaller
    } else {
        & $VenvPip install -r "$ScriptDir\requirements.txt" -r "$ScriptDir\requirements-windows.txt" pyinstaller
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Dependency installation failed (exit code $LASTEXITCODE)."
        exit 1
    }
    if (Test-Path "$VenvDir\Scripts\pywin32_postinstall.py") {
        & $VenvPython "$VenvDir\Scripts\pywin32_postinstall.py" -install -quiet | Out-Null
    }
    $Pywin32Sys32 = Join-Path $VenvDir "Lib\site-packages\pywin32_system32"
    if (Test-Path $Pywin32Sys32) {
        Copy-Item (Join-Path $Pywin32Sys32 "*.dll") (Join-Path $VenvDir "Scripts") -Force -ErrorAction SilentlyContinue
        Copy-Item (Join-Path $Pywin32Sys32 "*.dll") (Join-Path $VenvDir "Lib\site-packages\win32") -Force -ErrorAction SilentlyContinue
    }
    Set-Content -Path $BuildInstalledMarker -Value (Get-Date -Format "o")
    Write-Host "[itt1] Dependencies and PyInstaller ready." -ForegroundColor Green
}

# 3.5 Ensure libmpv-2.dll (ローカル再生エンジン用。リポジトリにはコミットしない)
$MpvDll = Join-Path $ScriptDir "libmpv-2.dll"
if (-not (Test-Path $MpvDll)) {
    Write-Host "[itt1] libmpv-2.dll not found. Downloading mpv dev archive..." -ForegroundColor Cyan
    try {
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/shinchiro/mpv-winbuild-cmake/releases/latest"
        $asset = $rel.assets | Where-Object { $_.name -match '^mpv-dev-x86_64-.*\.7z$' } | Select-Object -First 1
        if (-not $asset) {
            Write-Error "mpv dev archive not found in the latest shinchiro release."
            exit 1
        }
        $archive = Join-Path $env:TEMP $asset.name
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $archive
        $extractDir = Join-Path $env:TEMP "itt1-mpv-dev"
        Remove-Item $extractDir -Recurse -Force -ErrorAction SilentlyContinue
        # Windows 10+ 同梱の tar(bsdtar) は .7z を展開できる
        tar -xf $archive -C (New-Item -ItemType Directory -Force -Path $extractDir).FullName
        $found = Get-ChildItem $extractDir -Recurse -Filter "libmpv-2.dll" | Select-Object -First 1
        if (-not $found) {
            Write-Error "libmpv-2.dll not found inside the mpv dev archive."
            exit 1
        }
        Copy-Item $found.FullName $MpvDll
        Write-Host "[itt1] libmpv-2.dll acquired." -ForegroundColor Green
    } catch {
        Write-Error "Failed to acquire libmpv-2.dll: $_"
        exit 1
    }
}

# 4. Build exe with PyInstaller
Write-Host "[itt1] Building executable with PyInstaller..." -ForegroundColor Cyan
$ExePath = Join-Path $ScriptDir "dist\iTunesController.exe"
# Remove any stale exe so a failed build cannot be mistaken for success.
Remove-Item $ExePath -Force -ErrorAction SilentlyContinue
# Invoke via `python -m PyInstaller`: the pyinstaller.exe trampoline generates
# \\?\UNC\ prefixed paths that fail to load DLLs in PyInstaller's isolated
# child processes when the repo lives on a UNC share (e.g. \\wsl.localhost).
if (Test-Path "$ScriptDir\iTunesController.spec") {
    & $VenvPython -m PyInstaller --noconfirm "$ScriptDir\iTunesController.spec"
} else {
    & $VenvPython -m PyInstaller --noconfirm --onefile --windowed --name iTunesController main.py
}
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller failed (exit code $LASTEXITCODE)."
    exit 1
}

if (-not (Test-Path $ExePath)) {
    Write-Error "Build failed: $ExePath not found."
    exit 1
}

Write-Host "[itt1] Build successful! Output: $ExePath" -ForegroundColor Green

# 5. Install / Run
if ($Install) {
    Write-Host "[itt1] Triggering installation..." -ForegroundColor Cyan
    $InstallArgs = @("-ExecutionPolicy", "Bypass", "-File", (Join-Path $ScriptDir "install.ps1"))
    if ($Run) { $InstallArgs += "-Run" }
    & powershell $InstallArgs
} elseif ($Run) {
    Write-Host "[itt1] Launching $ExePath..." -ForegroundColor Green
    Start-Process -FilePath $ExePath
}
