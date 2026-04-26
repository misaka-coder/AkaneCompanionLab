@echo off
setlocal

cd /d "%~dp0"
echo [INFO] 正在启动 Akane 桌宠...
echo [INFO] 请先在另一个窗口运行: python launch_akane_memory_v01.py
echo.

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_akane_desktop_pet.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] Launcher exited with code %EXIT_CODE%.
)
pause

exit /b %EXIT_CODE%
