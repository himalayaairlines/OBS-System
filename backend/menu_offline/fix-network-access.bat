@echo off
rem Allows phones on the same Wi-Fi to reach the menu server.
rem Re-launches itself with administrator rights.
net session >nul 2>&1
if %errorlevel% neq 0 (
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\fix-network-access.ps1"
pause
