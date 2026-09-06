@echo off
setlocal
title Momentum Lab V2.1 Diagnostic Launcher
echo ============================================================
echo Momentum Lab V2.1 - Diagnostic Launcher
echo ============================================================
echo.
echo This launcher keeps a console window visible so startup errors
echo can be seen immediately.
echo.
if exist MomentumLabV2.exe (
    MomentumLabV2.exe
) else if exist MomentumLabV2.py (
    python MomentumLabV2.py
) else (
    echo ERROR: MomentumLabV2.exe / MomentumLabV2.py not found.
)
echo.
echo If the app failed, also inspect:
echo   logs\momentumlab.log
echo.
pause
