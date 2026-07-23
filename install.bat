@echo off
title PDF to AI Film - Install Dependencies

cd /d "%~dp0"

REM Project-local .venv, same path on any machine (no per-user hardcoded path)
set VENV_DIR=.venv
set PYTHON=%VENV_DIR%\Scripts\python.exe
set PYTHON_ABS=%CD%\%PYTHON%

if not exist "%PYTHON%" (
    echo [INFO] Project venv not found, creating %VENV_DIR% ...
    where py >nul 2>&1
    if %errorlevel%==0 (
        REM Python 3.14 ensurepip may hang indefinitely on some Windows systems.
        REM Create the venv without pip, then let the working system pip manage it.
        py -3 -m venv --without-pip "%VENV_DIR%"
    ) else (
        python -m venv --without-pip "%VENV_DIR%"
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

echo [INFO] Installing into %VENV_DIR% with the system pip...
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 -m pip --python "%PYTHON_ABS%" install --disable-pip-version-check --timeout 20 --retries 2 --progress-bar on -r requirements.txt
) else (
    python -m pip --python "%PYTHON_ABS%" install --disable-pip-version-check --timeout 20 --retries 2 --progress-bar on -r requirements.txt
)

if errorlevel 1 (
    echo.
    echo [ERROR] Dependency installation failed. Check the pip error above.
    pause
    exit /b 1
)

echo.
echo Done. Double-click run.bat to start the server.
pause
