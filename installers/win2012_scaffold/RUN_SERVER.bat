@echo off
setlocal
title Endpoint Management Server - Run
cd /d "%~dp0server"
set "PY="
if exist "D:\Python\python.exe" set "PY=D:\Python\python.exe"
if not defined PY set "PY=python"
echo Running pre-flight diagnostics...
"%PY%" doctor.py
if errorlevel 1 (
  echo.
  echo Server was NOT started because diagnostics failed.
  pause
  exit /b 1
)
echo.
echo Diagnostics passed. Starting server...
"%PY%" run_server.py
