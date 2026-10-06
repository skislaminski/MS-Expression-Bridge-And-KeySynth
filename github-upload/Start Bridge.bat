@echo off
rem Double-click in Explorer: starts the bridge and opens the interface in the browser.
cd /d "%~dp0"
".venv\Scripts\python.exe" -c "import mido, rtmidi, yaml" >nul 2>nul
if errorlevel 1 (
  echo Setting up the Python environment...
  where py >nul 2>nul
  if errorlevel 1 (python -m venv .venv) else (py -3 -m venv .venv)
  if errorlevel 1 goto failed
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" bridge.py --open
echo.
echo The bridge has stopped.
pause
exit /b

:failed
echo.
echo Setup failed. Install Python 3.11 or newer from python.org and try again.
pause
