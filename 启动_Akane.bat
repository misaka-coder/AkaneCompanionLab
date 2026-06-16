@echo off
setlocal

call "%~dp0start_akane.bat" %*
exit /b %ERRORLEVEL%
