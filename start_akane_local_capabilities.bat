@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_akane_local_capabilities.ps1" %*

if errorlevel 1 (
  echo.
  echo Akane local capabilities failed to start. Press any key to close...
  pause >nul
)
