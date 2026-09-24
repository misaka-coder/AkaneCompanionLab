@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0clean-user-caches.ps1" %*
exit /b %ERRORLEVEL%
