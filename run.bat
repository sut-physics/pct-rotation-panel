@echo off
cd /d "%~dp0gui"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 pct_panel.py
) else (
  python pct_panel.py
)
if errorlevel 1 pause
