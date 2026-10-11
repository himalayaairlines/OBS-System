@echo off
REM Load an existing obs.db (from the old non-Docker install) into the Docker volume.
REM Usage: import-db.bat C:\path\to\obs.db
setlocal
cd /d "%~dp0"
if "%~1"=="" ( echo Usage: import-db.bat C:\path\to\obs.db & pause & exit /b 1 )
if not exist "%~1" ( echo File not found: %~1 & pause & exit /b 1 )
echo This REPLACES the database inside Docker with %~1.
echo Run backup.bat first if the current data matters.
choice /m "Continue"
if errorlevel 2 exit /b 1

docker compose up -d backend >nul
docker compose stop backend
docker compose cp "%~1" backend:/data/obs.db
REM Remove stale WAL files from the old database and hand the file to the app user.
docker compose run --rm --no-deps --user root --entrypoint sh backend -c "rm -f /data/obs.db-wal /data/obs.db-shm && chown obs:obs /data/obs.db"
docker compose up -d
echo Imported. The database is upgraded to the current schema on start.
pause
