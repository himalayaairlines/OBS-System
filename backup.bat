@echo off
REM Copy the live database and encryption key into .\backups\<timestamp>\
setlocal
cd /d "%~dp0"
for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set STAMP=%%t
set TARGET=backups\%STAMP%
mkdir "%TARGET%" 2>nul
REM A consistent snapshot even while the app is running (SQLite online backup).
docker compose exec -T backend python -c "import sqlite3; s=sqlite3.connect('/data/obs.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); d.close(); s.close()"
if errorlevel 1 ( echo Backup failed: is the app running? & pause & exit /b 1 )
docker compose cp backend:/data/backup.db "%TARGET%\obs.db"
docker compose cp backend:/data/.obs_secret.key "%TARGET%\.obs_secret.key" 2>nul
docker compose exec -T backend rm -f /data/backup.db
echo Saved to %TARGET%. Keep the key file somewhere separate from the database.
pause
