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

rem server\.env is created automatically by the server on first start (LAN IP auto-detected)

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
echo Waiting for the server to start ...
call :waitport
if not errorlevel 1 goto :running
echo Service unavailable - starting in the background instead ^(survives closing this window^) ...
sc stop EndpointMgmtServer >nul 2>&1
set "PYW=%PY%"
if /i "%PY%"=="python" set "PYW=pythonw"
if /i not "%PY%"=="python" set "PYW=%PY:python.exe=pythonw.exe%"
start "VoyagerServer" "%PYW%" run_server.py --no-browser
call :waitport
if errorlevel 1 goto :not_started

:running
start "" http://127.0.0.1:9084/
echo.
echo Admin console: http://127.0.0.1:9084/   (you can close this window now)
echo First-run login: superadmin@platform.local  (see server\FIRST_RUN.txt)
echo.
pause
goto :eof

:not_started
echo.
echo [ERROR] The server did not start listening on port 9084.
echo Last lines of server\logs\server.log :
powershell -NoProfile -Command "if (Test-Path 'logs\server.log') { Get-Content 'logs\server.log' -Tail 25 }"
echo.
echo To see the error directly, run:  "%PY%" run_server.py   from the server folder.
goto :fail

rem ---- wait up to ~30s for port 9084 to be listening; errorlevel 0 = up ----
:waitport
set /a "WAITS=0"
:waitport_loop
netstat -an | findstr /R /C:":9084 .*LISTENING" >nul 2>&1
if not errorlevel 1 exit /b 0
set /a "WAITS+=1"
if %WAITS% GEQ 15 exit /b 1
ping -n 3 127.0.0.1 >nul
goto :waitport_loop

:fail

rem server\.env is created automatically by the server on first start (LAN IP auto-detected)

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
