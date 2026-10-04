@echo off
setlocal
title Endpoint Management Server - Control

cd /d "%~dp0server"

set "PY="
if exist "D:\Python\python.exe" set "PY=D:\Python\python.exe"
if not defined PY set "PY=python"

:menu
cls
echo ================================================================
echo   Endpoint Management Server - Control Panel
echo ================================================================
echo.
echo   1. Run server in this window
echo   2. Run diagnostics
echo   3. Install Windows service
echo   4. Start Windows service
echo   5. Stop Windows service
echo   6. Remove Windows service
echo   7. Open Admin Console
echo   8. Exit
echo.
set /p choice=Select option: 

if "%choice%"=="1" "%PY%" run_server.py
if "%choice%"=="2" "%PY%" doctor.py
if "%choice%"=="3" "%PY%" run_server.py service install
if "%choice%"=="4" "%PY%" run_server.py service start
if "%choice%"=="5" "%PY%" run_server.py service stop
if "%choice%"=="6" "%PY%" run_server.py service remove
if "%choice%"=="7" start "" "http://127.0.0.1:9084/"
if "%choice%"=="8" exit /b 0
pause
goto menu
