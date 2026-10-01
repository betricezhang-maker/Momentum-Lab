@echo off
setlocal
cd /d "%~dp0"
set "MOMENTUM_BUILD_PYTHON=%~1"
if not defined MOMENTUM_BUILD_PYTHON set "MOMENTUM_BUILD_PYTHON=python"
"%MOMENTUM_BUILD_PYTHON%" tools\build_portable.py
if errorlevel 1 (
  echo BUILD FAILED. See the error above. Existing distributions and data were preserved.
  exit /b 1
)
exit /b 0
