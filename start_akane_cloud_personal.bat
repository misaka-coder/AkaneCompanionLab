@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_akane_cloud_personal.ps1" %*

if errorlevel 1 (
  echo.
  echo Akane cloud personal launcher failed. Press any key to close...
  pause >nul
)
