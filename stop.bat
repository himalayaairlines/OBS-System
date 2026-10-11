@echo off
REM Stop Himalaya OBS. Data is kept on the obs-data volume.
cd /d "%~dp0"
docker compose down
echo Stopped. Your data is kept; run start.bat to start again.
timeout /t 5 >nul
