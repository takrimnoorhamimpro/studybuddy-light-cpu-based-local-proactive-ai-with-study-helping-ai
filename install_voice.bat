@echo off
cd /d "%~dp0"
echo Installing optional voice and tray packages. This may take a while.
py -3.12 -m pip install pyttsx3 sounddevice numpy faster-whisper pystray pillow psutil
if errorlevel 1 (
  echo Some packages could not be installed. Study Buddy text chat will still work.
  pause
  exit /b 1
)
echo Voice and tray dependencies installed. Speech model downloads on first voice use.
pause
