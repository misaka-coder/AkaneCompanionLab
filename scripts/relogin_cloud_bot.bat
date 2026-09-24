@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%relogin_cloud_bot.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
  echo Relogin did not complete. Keep this window open and check the reason above.
) else (
  echo Relogin workflow completed.
)
echo Press any key to close...
pause >nul

exit /b %EXIT_CODE%
