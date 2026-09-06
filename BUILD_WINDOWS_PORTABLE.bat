@echo off
setlocal
title Build Momentum Lab V2 Portable
echo ============================================================
echo Momentum Lab V3.2 - One-Time Windows Portable Build
echo ============================================================
echo.
echo Python is required only on THIS BUILD COMPUTER.
echo The generated dist\MomentumLabV2 folder is self-contained.
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo ERROR: Python 3.11 or 3.12 is required on the build computer.
  pause
  exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 goto :fail
python -m pip install pyinstaller pandas numpy matplotlib requests
if errorlevel 1 goto :fail

if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

python -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name MomentumLabV2 ^
  --collect-all matplotlib ^
  --add-data "ui;ui" ^
  MomentumLabV2.py

if errorlevel 1 goto :fail

if not exist dist\MomentumLabV2\config mkdir dist\MomentumLabV2\config
if not exist dist\MomentumLabV2\data mkdir dist\MomentumLabV2\data
if not exist dist\MomentumLabV2\results mkdir dist\MomentumLabV2\results
if not exist dist\MomentumLabV2\logs mkdir dist\MomentumLabV2\logs
copy README.txt dist\MomentumLabV2\README.txt >nul
copy RUN_DIAGNOSTIC.bat dist\MomentumLabV2\RUN_DIAGNOSTIC.bat >nul

echo.
echo BUILD COMPLETE
echo.
echo Portable folder:
echo   %CD%\dist\MomentumLabV2
echo.
echo Copy that whole folder to another Windows PC.
echo Run MomentumLabV2.exe.
echo No Python installation is required on the TARGET PC.
echo.
pause
exit /b 0

:fail
echo BUILD FAILED.
pause
exit /b 1
