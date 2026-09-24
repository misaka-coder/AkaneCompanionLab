@echo off
setlocal

cd /d "%~dp0"
echo [INFO] 正在启动 AkaneCompanionLab 主界面...
echo [INFO] 这个启动器会打开新的 gal 主界面，不是旧资源预览页。
echo.

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_akane_preview.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
    echo.
    echo [ERROR] Launcher exited with code %EXIT_CODE%.
    pause
)

exit /b %EXIT_CODE%
