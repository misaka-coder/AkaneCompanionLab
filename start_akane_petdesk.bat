@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_petdesk_runtime.ps1" %*
exit /b %ERRORLEVEL%
