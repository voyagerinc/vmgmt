@echo off
setlocal EnableExtensions EnableDelayedExpansion
title Voyager Management Server - Start

echo ================================================================
echo   Voyager Management Server - One-Click Start
echo   Auto-detects Windows and installs what is needed for this OS.
echo ================================================================
echo.

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo [ERROR] Right-click START.bat and choose "Run as administrator".
  pause & exit /b 1
)

cd /d "%~dp0"

rem ---- detect Windows major version (10 = Win10/Server2016+, 6 = Server2012/2012R2) ----
set "OSMAJ="
for /f %%v in ('powershell -NoProfile -Command "[Environment]::OSVersion.Version.Major" 2^>nul') do set "OSMAJ=%%v"
if not defined OSMAJ set "OSMAJ=6"
echo Detected Windows major version: %OSMAJ%

rem ---- find a compatible Python (3.8+) ----
set "PY="
if exist "D:\Python\python.exe" set "PY=D:\Python\python.exe"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if defined PY (
  "%PY%" -c "import sys;exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>&1 || set "PY="
)

if not defined PY (
  echo No compatible Python found - installing automatically for this OS...
  if "%OSMAJ%"=="10" (
    set "PYURL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    set "PYEXE=C:\Program Files\Python311\python.exe"
  ) else (
    set "PYURL=https://www.python.org/ftp/python/3.8.10/python-3.8.10-amd64.exe"
    set "PYEXE=C:\Program Files\Python38\python.exe"
  )
  set "PYINST=%TEMP%\voyager_python_setup.exe"
  echo Downloading !PYURL! ...
  powershell -NoProfile -Command "try{Invoke-WebRequest -UseBasicParsing '!PYURL!' -OutFile '!PYINST!'}catch{exit 1}"
  if errorlevel 1 ( echo [ERROR] Could not download Python. Check the server's internet access. & pause & exit /b 1 )
  echo Installing Python silently ^(this can take a minute^) ...
  "!PYINST!" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_launcher=1
  if exist "!PYEXE!" ( set "PY=!PYEXE!" ) else ( set "PY=python" )
)

echo.
echo Using Python:
"%PY%" --version
if errorlevel 1 ( echo [ERROR] Python is not usable. & pause & exit /b 1 )

cd server

rem server\.env is created automatically by the server on first start (LAN IP auto-detected)

echo.
echo [1/5] Upgrading pip ...
"%PY%" -m pip install --disable-pip-version-check --upgrade "pip<26" >nul 2>&1

echo [2/5] Installing dependencies (binary wheels only) ...
"%PY%" -m pip install --disable-pip-version-check --only-binary=:all: -r requirements.txt
if errorlevel 1 ( echo [ERROR] Dependency install failed. See messages above. & pause & exit /b 1 )

echo [3/5] Pre-flight checks ...
"%PY%" doctor.py

echo [4/5] Opening Windows Firewall port 9084 ...
netsh advfirewall firewall add rule name="Voyager Endpoint Server 9084" dir=in action=allow protocol=TCP localport=9084 profile=any >nul 2>&1

echo [5/5] Installing and starting the Windows service ...
"%PY%" server_service.py install >nul 2>&1
sc config EndpointMgmtServer start= auto >nul 2>&1
sc start EndpointMgmtServer >nul 2>&1
sc query EndpointMgmtServer | find "RUNNING" >nul 2>&1
if not errorlevel 1 (
  start "" http://127.0.0.1:9084/
  echo.
  echo ================================================================
  echo   Server is running as a Windows SERVICE.
  echo   It keeps running after you close this window, and auto-starts on boot.
  echo   Admin console : http://127.0.0.1:9084/
  echo   First-run login: server\FIRST_RUN.txt
  echo   To update later: run UPDATE.bat as administrator.
  echo ================================================================
  pause
  goto :eof
)

echo Service mode unavailable - starting in the BACKGROUND instead ...
rem detached, no console window -> survives closing this window (pythonw = no window)
set "PYW=%PY%"
if /i "%PY%"=="python" ( set "PYW=pythonw" ) else ( set "PYW=%PY:python.exe=pythonw.exe%" )
start "VoyagerServer" "%PYW%" run_server.py --no-browser
start "" http://127.0.0.1:9084/
echo.
echo ================================================================
echo   Server started in the background (survives closing this window).
echo   Admin console : http://127.0.0.1:9084/
echo   Logs          : server\logs\server.log
echo   To stop it: open Task Manager and end the pythonw.exe process,
echo   or use SERVER_CONTROL.bat.
echo ================================================================
pause
