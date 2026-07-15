@echo off
REM ============================================================================
REM  RESUME_QUILL - release the kill switch.
REM
REM  Deletes the kill-switch file so Quill can respond again. If STOP_QUILL also
REM  stopped the server, start it again afterwards (start.bat).
REM ============================================================================
setlocal
set "KS=%USERPROFILE%\.localmind\STOP"
if exist "%KS%" (
  del /f /q "%KS%"
  echo.
  echo   Kill switch RELEASED. Start the server again if it was stopped.
) else (
  echo.
  echo   Kill switch was not engaged - nothing to do.
)
echo.
pause
