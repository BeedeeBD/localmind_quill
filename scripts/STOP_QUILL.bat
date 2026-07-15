@echo off
REM ============================================================================
REM  STOP_QUILL - the kill switch.
REM
REM  Double-click to stop Quill immediately. It does two things:
REM    1. Creates the kill-switch file (%USERPROFILE%\.localmind\STOP). While it
REM       exists, Quill refuses every message - even if the server is running.
REM    2. Best-effort stops the running server on the default port (8000).
REM
REM  Quill cannot see, create, or delete this file or this script: killswitch.py
REM  and these scripts are HIDDEN in guardrails.py, and the file lives outside
REM  the project tree. Release the switch with RESUME_QUILL.bat.
REM ============================================================================
setlocal
set "KSDIR=%USERPROFILE%\.localmind"
if not exist "%KSDIR%" mkdir "%KSDIR%"
> "%KSDIR%\STOP" echo Quill stopped by kill switch on %DATE% %TIME%

echo.
echo   KILL SWITCH ENGAGED.
echo   Quill will refuse every message until you run RESUME_QUILL.bat.
echo.

REM Best-effort: also stop the local server listening on the default port.
set "PORT=8000"
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
  echo   Stopping server process (PID %%P) ...
  taskkill /PID %%P /F >nul 2>&1
)

echo.
pause
