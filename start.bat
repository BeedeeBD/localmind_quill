@echo off
REM Double-click this to launch localmind. It activates the virtual environment,
REM starts the server, and opens the chat UI in your browser.
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo Could not find .venv. Create it first with:  py -3.12 -m venv .venv
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"

echo Starting localmind on http://127.0.0.1:8000  (press Ctrl+C to stop)
REM Open the browser a few seconds later, once the server is up.
start "" /min cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:8000"

python -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
