@echo off
setlocal EnableExtensions
title Voyager Management Server - Update from License Server

echo ================================================================
echo   Voyager Management Server - Update
echo   Pulls the latest build from the license server and restarts.
echo ================================================================
echo.

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo [ERROR] Right-click this file and choose "Run as administrator".
  pause & exit /b 1
)

cd /d "%~dp0"

rem --- read EMP_LICENSE_SERVER from server\.env ---
set "LIC="
if exist "server\.env" (
  for /f "usebackq tokens=1,* delims==" %%a in ("server\.env") do (
    if /i "%%a"=="EMP_LICENSE_SERVER" set "LIC=%%b"
  )
)
if not defined LIC (
  echo [ERROR] EMP_LICENSE_SERVER is not set in server\.env
  echo         e.g.  EMP_LICENSE_SERVER=http://vmgmt.voyager.co.in:9084
  pause & exit /b 1
)
set "LIC=%LIC: =%"
echo License server: %LIC%

rem --- detect Python (check server venv, D:\Python, or system python) ---
set "PY="
if exist "%~dp0server\.venv\Scripts\python.exe" set "PY=%~dp0server\.venv\Scripts\python.exe"
if not defined PY if exist "D:\Python\python.exe" set "PY=D:\Python\python.exe"
if not defined PY ( where python >nul 2>&1 & if not errorlevel 1 set "PY=python" )

set "TMPZIP=%~dp0voyager_update.zip"
set "TMPDIR=%~dp0voyager_update_tmp"

echo.
echo [1/5] Downloading latest bundle ...
if exist "%TMPZIP%" del /f /q "%TMPZIP%" >nul 2>&1
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11 -bor [Net.SecurityProtocolType]::Tls; try { (New-Object System.Net.WebClient).DownloadFile('%LIC%/api/updates/download/bundle', '%TMPZIP%') } catch { try { Invoke-WebRequest -UseBasicParsing -Uri '%LIC%/api/updates/download/bundle' -OutFile '%TMPZIP%' } catch { exit 1 } }"
if not exist "%TMPZIP%" (
  echo [ERROR] Download failed from %LIC%/api/updates/download/bundle.
  pause & exit /b 1
)

echo [2/5] Stopping service ...
net stop EndpointMgmtServer >nul 2>&1
taskkill /f /im python.exe >nul 2>&1
timeout /t 2 /nobreak >nul 2>&1

echo [3/5] Extracting (your data and .env are preserved) ...
if exist "%TMPDIR%" rmdir /s /q "%TMPDIR%"
mkdir "%TMPDIR%" >nul 2>&1

rem --- Extract using Python zipfile module ---
if defined PY (
  "%PY%" -c "import zipfile, sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "%TMPZIP%" "%TMPDIR%" >nul 2>&1
)
if not exist "%TMPDIR%\server\app\main.py" (
  python -c "import zipfile, sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "%TMPZIP%" "%TMPDIR%" >nul 2>&1
)
if not exist "%TMPDIR%\server\app\main.py" (
  powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try{Add-Type -AssemblyName System.IO.Compression.FileSystem;[System.IO.Compression.ZipFile]::ExtractToDirectory('%TMPZIP%','%TMPDIR%')}catch{}" >nul 2>&1
)
if not exist "%TMPDIR%\server\app\main.py" (
  tar -xf "%TMPZIP%" -C "%TMPDIR%" >nul 2>&1
)

if not exist "%TMPDIR%\server\app\main.py" (
  echo [ERROR] Could not extract update zip.
  echo ZIP Path: %TMPZIP%
  echo DIR Path: %TMPDIR%
  pause & exit /b 1
)

rem --- replace application code only; never touch server\data or server\.env ---
robocopy "%TMPDIR%\server\app" "server\app" /MIR /NFL /NDL /NJH /NJS /NC /NS >nul
if exist "%TMPDIR%\server\run_server.py" copy /y "%TMPDIR%\server\run_server.py" "server\run_server.py" >nul
if exist "%TMPDIR%\server\server_service.py" copy /y "%TMPDIR%\server\server_service.py" "server\server_service.py" >nul
if exist "%TMPDIR%\server\requirements.txt" copy /y "%TMPDIR%\server\requirements.txt" "server\requirements.txt" >nul
if exist "%TMPDIR%\server\doctor.py" copy /y "%TMPDIR%\server\doctor.py" "server\doctor.py" >nul

rem clean up temp artifacts
if exist "%TMPZIP%" del /f /q "%TMPZIP%" >nul 2>&1
if exist "%TMPDIR%" rmdir /s /q "%TMPDIR%" >nul 2>&1

echo [4/5] Updating dependencies ...
if defined PY (
  pushd server
  "%PY%" -m pip install --disable-pip-version-check --only-binary=:all: -r requirements.txt
  popd
) else (
  echo [WARN] Python not found - skipped dependency update.
)

echo [5/5] Starting service ...
net start EndpointMgmtServer >nul 2>&1
if errorlevel 1 (
  if defined PY ( pushd server & start "Voyager Server" "%PY%" run_server.py & popd )
)

echo.
echo ================================================================
echo   Update complete. Open the admin console and check
echo   Settings -> Software updates for the new version/date.
echo ================================================================
pause
