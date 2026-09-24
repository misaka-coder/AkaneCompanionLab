@echo off
setlocal
chcp 65001 >nul

echo Akane Bot emergency recovery
echo   status   - check both Bots
echo   personal - recover Personal Bot
echo   finance  - recover Finance Bot
echo   both     - recover both Bots
set /p ACTION=Choose [status/personal/finance/both]:

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0mobile_relogin_cloud_bot.ps1" -HostName "__HOST__" -Bot "%ACTION%"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" echo Recovery did not complete. Check the message above.
pause
exit /b %EXIT_CODE%
