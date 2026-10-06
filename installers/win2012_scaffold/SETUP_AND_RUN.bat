@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Endpoint Management Server - Windows Setup

echo ================================================================
echo   Endpoint Management Server - One Click Setup
echo   Windows Server 2012 / 2016 / 2022
echo ================================================================
echo.

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo [ERROR] Please right-click this file and choose "Run as administrator".
  pause
  exit /b 1
)

cd /d "%~dp0server"

set "PY="
if exist "D:\Python\python.exe" set "PY=D:\Python\python.exe"
if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY (
  echo [ERROR] Python was not found.
  echo.
  echo Windows Server 2012: install Python 3.8.10 x64.
  echo Windows Server 2016/2022: Python 3.8+ is supported by this source.
  pause
  exit /b 1
)

echo [1/6] Python:
"%PY%" --version
if errorlevel 1 goto :fail

if not exist ".env" (
  echo Creating default configuration: .env ...
  >  ".env" echo EMP_HOST=0.0.0.0
  >> ".env" echo EMP_PORT=9084
  >> ".env" echo EMP_SERVER_PUBLIC_URL=http://THIS-SERVER-IP:9084
  >> ".env" echo EMP_LICENSE_SERVER=http://vmgmt.voyager.co.in:9084
  >> ".env" echo EMP_DEPLOYMENT_MODEL=on_premise
  echo   NOTE: edit .env and set EMP_SERVER_PUBLIC_URL to this server's IP.
)

echo [2/6] Upgrading pip...
"%PY%" -m pip install --upgrade "pip<26" 
if errorlevel 1 goto :fail

echo [3/6] Installing pinned dependencies...
echo       Binary wheels only - no compiler/pg_config build.
"%PY%" -m pip install --only-binary=:all: -r requirements.txt
if errorlevel 1 goto :fail

echo [4/6] Running application pre-flight checks...
"%PY%" doctor.py
if errorlevel 1 goto :fail

echo [5/6] Opening Windows Firewall port 9084...
netsh advfirewall firewall show rule name="Endpoint Management Server 9084" >nul 2>&1
if errorlevel 1 (
  netsh advfirewall firewall add rule name="Endpoint Management Server 9084" dir=in action=allow protocol=TCP localport=9084 profile=any >nul
)
echo Firewall rule ready.

echo [6/6] Installing + starting the Management Server (as a service / background)...
"%PY%" server_service.py install >nul 2>&1
sc config EndpointMgmtServer start= auto >nul 2>&1
sc start EndpointMgmtServer >nul 2>&1
sc query EndpointMgmtServer | find "RUNNING" >nul 2>&1
if not errorlevel 1 (
  echo Server running as a Windows SERVICE - keeps running after you close this window.
) else (
  echo Service unavailable - starting in the background instead ^(survives closing this window^) ...
  set "PYW=%PY%"
  if /i "%PY%"=="python" ( set "PYW=pythonw" ) else ( set "PYW=%PY:python.exe=pythonw.exe%" )
  start "VoyagerServer" "!PYW!" run_server.py --no-browser
)
start "" http://127.0.0.1:9084/
echo.
echo Admin console: http://127.0.0.1:9084/   (you can close this window now)
echo.
pause
goto :eof

:fail
echo.
echo ================================================================
echo SETUP FAILED. No partial application changes were hidden.
echo Review the [FAIL]/ERROR output above.
echo ================================================================
pause
exit /b 1
