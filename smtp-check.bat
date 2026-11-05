@echo off
rem Tests the SMTP settings stored in OBS from inside the running backend
rem container: DNS, connection, TLS and login. Changes nothing, sends nothing.
cd /d "%~dp0"
docker compose ps backend >nul 2>&1 || (echo OBS is not running. Start it with start.bat first. & pause & exit /b 1)
docker compose exec -T backend python - < "%~dp0backend\smtp_check.py"
echo.
pause
