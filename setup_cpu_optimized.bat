@echo off
cd /d "%~dp0"
echo Study Buddy CPU-optimized setup
echo This downloads the single primary chat model used by the native-tool architecture.
echo.
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama was not found. Install Ollama first: https://ollama.com/download/windows
  pause
  exit /b 1
)
ollama pull qwen2.5:1.5b
if errorlevel 1 (
  echo Chat model download failed. Check Ollama and your internet connection.
  echo You can retry manually with: ollama pull qwen2.5:1.5b
  pause
  exit /b 1
)
echo.
echo Model ready. Start Study Buddy with run_study_buddy.bat
echo Qwen 2.5 1.5B is the single primary model and chooses from the registered native tools.
echo No separate embedding model is required for normal chat or tool selection.
pause
