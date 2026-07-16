@echo off
title PDF to AI Film - Install Dependencies

cd /d "%~dp0"

REM Project-local .venv, same path on any machine (no per-user hardcoded path)
set VENV_DIR=.venv
set PYTHON=%VENV_DIR%\Scripts\python.exe

if not exist "%PYTHON%" (
    echo [INFO] Project venv not found, creating %VENV_DIR% ...
    where py >nul 2>&1
    if %errorlevel%==0 (
        py -3 -m venv "%VENV_DIR%"
    ) else (
        python -m venv "%VENV_DIR%"
    )
)

if not exist "%PYTHON%" (
    echo [ERROR] Failed to create venv. Install Python 3.10+ and add it to PATH, then retry.
    pause
    exit /b 1
)

echo ========================================
echo   Installing Python dependencies
echo ========================================
echo.

"%PYTHON%" -m pip install --upgrade pip
"%PYTHON%" -m pip install -r requirements.txt

echo.
echo Done. Double-click run.bat to start the server.
pause
