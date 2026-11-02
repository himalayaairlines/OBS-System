@echo off
setlocal EnableExtensions
title Himalaya Airlines - Koseli Menu Server
cd /d "%~dp0"
chcp 65001 >nul

rem ---- find a working Python 3.8+ (python, py launcher, python3) ----
set "PY="
for %%C in ("python" "py -3" "python3") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3,8) else 1)" >nul 2>&1 && set "PY=%%~C"
  )
)
if not defined PY goto :nopython

%PY% "%~dp0server.py" %*
if errorlevel 1 (
  echo.
  echo The server stopped with an error. See the message above.
  pause
)
exit /b

:nopython
echo.
echo  Python 3.8 or newer was not found on this computer.
echo.
echo  1. Download it from https://www.python.org/downloads/
echo  2. During setup, TICK the box "Add python.exe to PATH"
echo  3. Double-click start-server.bat again
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1
