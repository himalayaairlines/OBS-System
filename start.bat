@echo off
REM Himalaya OBS: build (first time) and start backend + web app, then open the browser.
setlocal
cd /d "%~dp0"
title Himalaya OBS

docker info >nul 2>&1
if errorlevel 1 (
  echo.
  echo  Docker is not running. Start Docker Desktop, wait until it says "Engine running", then run this again.
  echo.
  pause
  exit /b 1
)

if not exist ".env" copy ".env.example" ".env" >nul

set WEB_PORT=8080
set API_PORT=8005
for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do (
  if /i "%%a"=="WEB_PORT" set WEB_PORT=%%b
  if /i "%%a"=="API_PORT" set API_PORT=%%b
)

echo Building and starting Himalaya OBS (the first build takes a few minutes)...
docker compose up -d --build
if errorlevel 1 (
  echo.
  echo  Start failed. The messages above say why.
  pause
  exit /b 1
)

echo Waiting for the app to answer...
powershell -NoProfile -Command "for($i=0;$i -lt 90;$i++){try{if((Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://localhost:%WEB_PORT%/api/health).StatusCode -eq 200){exit 0}}catch{}; Start-Sleep 2}; exit 1"
if errorlevel 1 (
  echo  The app did not answer in time. Showing the last log lines:
  docker compose logs --tail 40
  pause
  exit /b 1
)

echo.
echo  Himalaya OBS is running.
echo    Web app : http://localhost:%WEB_PORT%
echo    API     : http://localhost:%API_PORT%/health   (iPad / Android app: http://THIS-SERVER-IP:%API_PORT%)
echo    Stop    : stop.bat
echo.
start "" "http://localhost:%WEB_PORT%"
timeout /t 8 >nul
