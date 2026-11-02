@echo off
cd /d "%~dp0"
echo ============================================================
echo  Study Buddy - Ollama server tuning (CPU-only PC)
echo ============================================================
echo.
echo This sets four user-level Windows environment variables that Ollama reads at startup:
echo.
echo   OLLAMA_NUM_PARALLEL=1        one request at a time (no duplicate conversation memory)
echo   OLLAMA_MAX_LOADED_MODELS=1   never keep a second model loaded in RAM
echo   OLLAMA_FLASH_ATTENTION=1     lighter, faster attention
echo   OLLAMA_KV_CACHE_TYPE=q8_0    halves the memory used by the conversation cache
echo.
echo Undo any time with reset_ollama_tuning.bat
echo.
choice /m "Apply these settings now"
if errorlevel 2 exit /b 0
setx OLLAMA_NUM_PARALLEL 1 >nul
setx OLLAMA_MAX_LOADED_MODELS 1 >nul
setx OLLAMA_FLASH_ATTENTION 1 >nul
setx OLLAMA_KV_CACHE_TYPE q8_0 >nul
echo.
echo Done. IMPORTANT: they only take effect after Ollama restarts.
echo   1. Right-click the Ollama icon in the system tray and choose Quit.
echo   2. Start Ollama again from the Start menu, then start Study Buddy.
echo   3. Compare reply times in Intelligence Center - Live Trace.
echo.
pause
