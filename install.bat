@echo off
rem Install requirements for gui\pct_panel.py on Windows (pyserial). Tkinter ships with python.org Python.
set "PY="
where py >nul 2>nul
if not errorlevel 1 (
  set "PY=py -3"
  goto found
)
where python >nul 2>nul
if not errorlevel 1 (
  set "PY=python"
  goto found
)
echo Python was not found. Install Python 3 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer, then run this file again.
pause
exit /b 1

:found
%PY% -c "import tkinter" >nul 2>nul
if errorlevel 1 (
  echo Tkinter is missing. Re-run the Python installer, choose Modify, and tick "tcl/tk and IDLE".
  pause
  exit /b 1
)
%PY% -m pip install --upgrade pyserial
if errorlevel 1 (
  echo pip install failed. Check your internet connection and try again.
  pause
  exit /b 1
)
echo.
echo Done. Start the program with run.bat
pause
