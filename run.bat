@echo off
title PDF to AI Film - Flask Server

cd /d "%~dp0"

REM Project-local .venv, same path on any machine
set PYTHON=.venv\Scripts\python.exe

if not exist "%PYTHON%" (
    echo [ERROR] Project venv not found at .venv
    echo Please run install.bat first to set up dependencies.
    pause
    exit /b 1
)

echo ========================================
echo   PDF to AI Film Generator
echo   Starting Flask server...
echo ========================================
echo.
echo Open in browser: http://127.0.0.1:5000
echo Press Ctrl+C to stop (or run stop.bat).
echo.

"%PYTHON%" app.py

pause
