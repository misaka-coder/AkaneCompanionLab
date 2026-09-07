@echo off
setlocal

where pwsh.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] PowerShell 7 is required. Install pwsh and try again.
  pause
  exit /b 1
)

pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_akane_local_qq_test.ps1" -OpenQQSetup %*
set "AKANE_LAUNCH_EXIT=%ERRORLEVEL%"
if not "%AKANE_LAUNCH_EXIT%"=="0" (
  echo.
  echo [ERROR] Local QQ startup failed. Keep this window for diagnosis.
  pause
)
exit /b %AKANE_LAUNCH_EXIT%
