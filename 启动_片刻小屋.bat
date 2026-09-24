@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_akane_scene.ps1"

exit /b %ERRORLEVEL%
