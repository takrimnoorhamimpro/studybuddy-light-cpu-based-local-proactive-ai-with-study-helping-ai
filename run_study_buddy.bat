@echo off
cd /d "%~dp0"
py -3.12 app\desktop_app.py
if errorlevel 1 (
  echo.
  echo Study Buddy stopped. Check the error above.
  pause
)
