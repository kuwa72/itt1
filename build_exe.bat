@echo off
cd /d "%~dp0"

echo [1/4] Preparing virtual environment (.venv)...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        pause
        exit /b 1
    )
)
set "VENV_PY=.venv\Scripts\python.exe"

echo [2/4] Installing dependencies...
"%VENV_PY%" -m pip install --upgrade pip
"%VENV_PY%" -m pip install -r requirements.txt
"%VENV_PY%" -m pip install -r requirements-windows.txt
"%VENV_PY%" -m pip install pyinstaller
if errorlevel 1 (
    echo Dependency installation failed.
    pause
    exit /b 1
)

echo [3/4] Building exe with PyInstaller...
"%VENV_PY%" -m PyInstaller --noconfirm --onefile --windowed --name iTunesController main.py
if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo [4/4] Done. Output: dist\iTunesController.exe
pause
