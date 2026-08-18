# iTunes Controller Installer script for Windows PowerShell
[CmdletBinding()]
param(
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\iTunesController",
    [switch]$NoDesktopShortcut,
    [switch]$NoStartMenuShortcut,
    [switch]$Run,
    [switch]$Uninstall,
    [switch]$ForceRebuild
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

# Resolve paths
$StartMenuDir = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Programs)
if (-not $StartMenuDir) {
    $StartMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
}

$DesktopDir = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
if (-not $DesktopDir) {
    $DesktopDir = Join-Path $env:USERPROFILE "Desktop"
}

$StartMenuShortcutPath = Join-Path $StartMenuDir "iTunes Controller.lnk"
$DesktopShortcutPath = Join-Path $DesktopDir "iTunes Controller.lnk"
$InstalledExe = Join-Path $InstallDir "iTunesController.exe"

# --- Uninstall ---
if ($Uninstall) {
    Write-Host "[itt1] Uninstalling iTunes Controller..." -ForegroundColor Cyan

    $Running = Get-Process -Name "iTunesController" -ErrorAction SilentlyContinue
    if ($Running) {
        Write-Host "[itt1] Stopping running iTunesController process..." -ForegroundColor Yellow
        $Running | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }

    if (Test-Path $StartMenuShortcutPath) {
        Remove-Item $StartMenuShortcutPath -Force
        Write-Host "[itt1] Removed Start Menu shortcut: $StartMenuShortcutPath" -ForegroundColor Gray
    }
    if (Test-Path $DesktopShortcutPath) {
        Remove-Item $DesktopShortcutPath -Force
        Write-Host "[itt1] Removed Desktop shortcut: $DesktopShortcutPath" -ForegroundColor Gray
    }

    if (Test-Path $InstallDir) {
        Remove-Item $InstallDir -Recurse -Force
        Write-Host "[itt1] Removed installation folder: $InstallDir" -ForegroundColor Gray
    }

    Write-Host "[itt1] Uninstall complete." -ForegroundColor Green
    exit 0
}

# --- Install ---
Write-Host "[itt1] Starting installation to $InstallDir..." -ForegroundColor Cyan

$DistExe = Join-Path $ScriptDir "dist\iTunesController.exe"
if ($ForceRebuild -or (-not (Test-Path $DistExe))) {
    Write-Host "[itt1] Building iTunesController.exe..." -ForegroundColor Cyan
    & powershell -ExecutionPolicy Bypass -File (Join-Path $ScriptDir "build.ps1")
    if ($LASTEXITCODE -ne 0 -or (-not (Test-Path $DistExe))) {
        Write-Error "Build failed. Cannot proceed with installation."
        exit 1
    }
}

$Running = Get-Process -Name "iTunesController" -ErrorAction SilentlyContinue
if ($Running) {
    Write-Host "[itt1] Stopping currently running iTunesController instance..." -ForegroundColor Yellow
    $Running | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Write-Host "[itt1] Created install directory: $InstallDir" -ForegroundColor Gray
}

Copy-Item -Path $DistExe -Destination $InstalledExe -Force
Write-Host "[itt1] Copied executable -> $InstalledExe" -ForegroundColor Green

$InstalledConfig = Join-Path $InstallDir "config.json"
$SourceConfig = Join-Path $ScriptDir "config.json"
if (Test-Path $SourceConfig) {
    if (-not (Test-Path $InstalledConfig)) {
        Copy-Item -Path $SourceConfig -Destination $InstalledConfig
        Write-Host "[itt1] Copied initial config.json -> $InstalledConfig" -ForegroundColor Gray
    } else {
        Write-Host "[itt1] Kept existing config.json at $InstalledConfig" -ForegroundColor Gray
    }
}

# Create Shortcuts
$WshShell = New-Object -ComObject WScript.Shell

if (-not $NoStartMenuShortcut) {
    try {
        $Shortcut = $WshShell.CreateShortcut($StartMenuShortcutPath)
        $Shortcut.TargetPath = $InstalledExe
        $Shortcut.WorkingDirectory = $InstallDir
        $Shortcut.Description = "iTunes Controller"
        $Shortcut.Save()
        Write-Host "[itt1] Start Menu shortcut created: $StartMenuShortcutPath" -ForegroundColor Green
    } catch {
        Write-Warning "Failed to create Start Menu shortcut: $_"
    }
}

if (-not $NoDesktopShortcut) {
    try {
        $Shortcut = $WshShell.CreateShortcut($DesktopShortcutPath)
        $Shortcut.TargetPath = $InstalledExe
        $Shortcut.WorkingDirectory = $InstallDir
        $Shortcut.Description = "iTunes Controller"
        $Shortcut.Save()
        Write-Host "[itt1] Desktop shortcut created: $DesktopShortcutPath" -ForegroundColor Green
    } catch {
        Write-Warning "Failed to create Desktop shortcut: $_"
    }
}

Write-Host "`n[itt1] Installation completed successfully!" -ForegroundColor Green
Write-Host "  Location:   $InstallDir" -ForegroundColor Gray
Write-Host "  Start Menu: $StartMenuShortcutPath" -ForegroundColor Gray
if (-not $NoDesktopShortcut) {
    Write-Host "  Desktop:    $DesktopShortcutPath" -ForegroundColor Gray
}

if ($Run) {
    Write-Host "`n[itt1] Launching installed application..." -ForegroundColor Green
    Start-Process -FilePath $InstalledExe -WorkingDirectory $InstallDir
}
