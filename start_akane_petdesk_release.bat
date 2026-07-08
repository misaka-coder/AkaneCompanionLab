@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_akane_petdesk_release.ps1" %*
exit /b %ERRORLEVEL%
