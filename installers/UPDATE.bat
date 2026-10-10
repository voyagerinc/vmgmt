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
  echo         e.g.  EMP_LICENSE_SERVER=http://vmgmt.voyager.co.in:8084
  pause & exit /b 1
)
set "LIC=%LIC: =%"
rem --- correct known-wrong addresses from older builds (license server is :8084) ---
set "LIC0=%LIC%"
set "LIC=%LIC:vmgmt.voyage.co.in=vmgmt.voyager.co.in%"
set "LIC=%LIC:vmgmt.voyager.co.in:9084=vmgmt.voyager.co.in:8084%"
if not "%LIC%"=="%LIC0%" (
  echo Correcting license server address in server\.env to %LIC%
  powershell -NoProfile -Command "$f='server\.env'; $t=[IO.File]::ReadAllText($f); $t=$t.Replace('vmgmt.voyage.co.in','vmgmt.voyager.co.in').Replace('vmgmt.voyager.co.in:9084','vmgmt.voyager.co.in:8084'); [IO.File]::WriteAllText($f,$t)"
)
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
set "ZSIZE=0"
if exist "%TMPZIP%" for %%F in ("%TMPZIP%") do set "ZSIZE=%%~zF"
if %ZSIZE% LSS 1000 (
  echo [ERROR] Download failed from %LIC%/api/updates/download/bundle.
  echo         Check that this server can open %LIC% in a browser.
  if exist "%TMPZIP%" del /f /q "%TMPZIP%" >nul 2>&1
  pause & exit /b 1
)

echo [2/5] Stopping the Management Server ...
net stop EndpointMgmtServer >nul 2>&1
rem stop whatever still serves port 9084 (python.exe, or pythonw.exe from a background start):
rem otherwise the OLD server keeps running with old code while the new files are on disk
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":9084 .*LISTENING"') do taskkill /f /pid %%p >nul 2>&1
ping -n 3 127.0.0.1 >nul
netstat -an | findstr /R /C:":9084 .*LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [ERROR] The old server is still running on port 9084 and could not be stopped.
  echo         Close it in Task Manager ^(python.exe / pythonw.exe^) and run UPDATE.bat again.
  pause & exit /b 1
)

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

echo [5/5] Starting the Management Server ...
net start EndpointMgmtServer >nul 2>&1
call :waitport
if not errorlevel 1 goto :started
rem no service: start in the background (no window), like SETUP_AND_RUN.bat
set "PYW=pythonw"
if defined PY if /i not "%PY%"=="python" set "PYW=%PY:python.exe=pythonw.exe%"
pushd server
start "VoyagerServer" "%PYW%" run_server.py --no-browser
popd
call :waitport
if errorlevel 1 (
  echo [ERROR] The server did not start on port 9084. Last lines of server\logs\server.log :
  powershell -NoProfile -Command "if (Test-Path 'server\logs\server.log') { Get-Content 'server\logs\server.log' -Tail 20 }"
  pause & exit /b 1
)
:started

echo.
echo ================================================================
echo   Update complete - the new version is running.
echo   In the browser press Ctrl+F5 on the admin console, then check
echo   Settings -^> Software updates for the new version/date.
echo ================================================================
pause
goto :eof

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
