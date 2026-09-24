@echo off
setlocal

rem Compatibility alias for the current Tauri desktop and its backend.
call "%~dp0start_akane.bat" -Mode Desktop %*
exit /b %ERRORLEVEL%
