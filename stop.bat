@echo off
title PDF to AI Film - Stop Server

echo ========================================
echo   Stop PDF to AI Film service (port 5000)
echo ========================================
echo.

REM Match ":5000 " (with trailing space) so ports like 50003/50004 are not killed
set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":5000 " ^| findstr "LISTENING"') do (
    echo Killing PID %%a ...
    taskkill /F /PID %%a >nul 2>&1
    set FOUND=1
)

if "%FOUND%"=="0" (
    echo No service found on port 5000 ^(maybe already stopped^).
) else (
    echo Service stopped.
)

echo.
pause
