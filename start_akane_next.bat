@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_akane_next.ps1" %*

if errorlevel 1 (
  echo.
  echo Akane Next launcher failed. Press any key to close...
  pause >nul
)
