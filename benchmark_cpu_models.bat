@echo off
cd /d "%~dp0"
py -3.12 scripts\benchmark_ollama.py --runs 3
if errorlevel 1 (
  echo Benchmark failed. Check that Ollama is running and Python dependencies are installed.
  pause
  exit /b 1
)
pause
