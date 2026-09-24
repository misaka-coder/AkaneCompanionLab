@echo off
setlocal

set "PROJECT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%scripts\bootstrap_akane_windows.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Akane failed to prepare or start.
)

exit /b %EXIT_CODE%
