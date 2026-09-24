@echo off
setlocal

rem No-argument double-click always uses the persisted public profile.
if "%~1"=="" goto public_start

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%start_akane_next.ps1" %*

if errorlevel 1 (
  echo.
  echo Akane Next launcher failed. Press any key to close...
  pause >nul
)

exit /b %ERRORLEVEL%

:public_start
call "%~dp0start_akane.bat"
exit /b %ERRORLEVEL%
