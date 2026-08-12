@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%sync_akane_local_test_from_cloud.ps1" %*

if errorlevel 1 (
  echo.
  echo Akane local-test cloud config sync failed. Press any key to close...
  pause >nul
)
