@echo off
rem Compatibility entry point; the safe versioned builder preserves existing data.
call "%~dp0build_portable.bat" %*
exit /b %errorlevel%
