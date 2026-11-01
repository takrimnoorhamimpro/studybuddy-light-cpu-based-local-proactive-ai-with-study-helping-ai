@echo off
cd /d "%~dp0"
echo Repairing the Study Buddy local chat model...
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama was not found. Install it first from https://ollama.com/download/windows
  pause
  exit /b 1
)
echo Pulling the exact 3B model tag used by Study Buddy: llama3.2:3b
echo This requires an internet connection and about 2 GB of model storage.
ollama pull llama3.2:3b
if errorlevel 1 (
  echo Model download failed. Confirm Ollama is running, then try: ollama pull llama3.2:3b
  pause
  exit /b 1
)
echo.
echo Installed models:
ollama list
echo.
echo Restart Study Buddy. The app can also resolve the equivalent llama3.2:latest alias automatically.
pause
