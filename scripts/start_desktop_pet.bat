@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%.."

powershell -NoProfile -ExecutionPolicy Bypass -File "%PROJECT_DIR%\start_akane_next.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Akane Next desktop launcher failed. Press any key to close...
  pause >nul
)

exit /b %EXIT_CODE%
