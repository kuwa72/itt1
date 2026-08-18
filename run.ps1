# iTunes Controller Launcher & venv setup script for Windows PowerShell
[CmdletBinding()]
param(
    [switch]$ForceReinstall,
    [string]$Config = "config.json"
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
        $PythonCmd = $null
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $PythonCmd = "py"
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            $PythonCmd = "python"
        } else {
            Write-Error "Python or uv was not found on your Windows PATH or Scoop. Please install Python or uv."
            exit 1
        }
        Write-Host "[itt1] Using $PythonCmd..." -ForegroundColor Gray
        & $PythonCmd -m venv $VenvDir
    }
}

# 3. Install/Update requirements
$InstalledMarker = Join-Path $VenvDir ".installed"
if ($ForceReinstall -or (-not (Test-Path $InstalledMarker))) {
    Write-Host "[itt1] Installing dependencies from requirements.txt and requirements-windows.txt..." -ForegroundColor Cyan
    if ($UvPath) {
        & $UvPath pip install --python $VenvPython -r "$ScriptDir\requirements.txt" -r "$ScriptDir\requirements-windows.txt"
    } else {
        & $VenvPip install -r "$ScriptDir\requirements.txt" -r "$ScriptDir\requirements-windows.txt"
    }
    Set-Content -Path $InstalledMarker -Value (Get-Date -Format "o")
    Write-Host "[itt1] Dependencies installed successfully." -ForegroundColor Green
}

# 4. Launch iTunes Controller
Write-Host "[itt1] Launching iTunes Controller..." -ForegroundColor Green
$LaunchArgs = @("main.py", "--config", $Config)
& $VenvPython $LaunchArgs
