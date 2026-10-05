@echo off
call "%~dp0start.cmd" -Lan %*
exit /b %ERRORLEVEL%
