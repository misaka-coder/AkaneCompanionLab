@echo off
setlocal

rem No-argument double-click always uses the persisted public profile.
if "%~1"=="" goto public_start

set "SCRIPT_DIR=%~dp0"
where pwsh.exe >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Akane local test requires PowerShell 7 ^(pwsh^).
  echo         Install PowerShell 7, then run this launcher again.
  exit /b 1
)

pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_akane_local_test.ps1" %*

if errorlevel 1 (
  echo.
  echo Akane pure-local test launcher failed. Press any key to close...
  pause >nul
)

exit /b %ERRORLEVEL%

:public_start
call "%~dp0start_akane.bat"
exit /b %ERRORLEVEL%
