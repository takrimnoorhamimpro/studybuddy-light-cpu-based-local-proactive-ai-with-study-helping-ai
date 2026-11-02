@echo off
echo Removing the Ollama tuning variables set by setup_ollama_tuning.bat ...
reg delete "HKCU\Environment" /v OLLAMA_NUM_PARALLEL /f >nul 2>nul
reg delete "HKCU\Environment" /v OLLAMA_MAX_LOADED_MODELS /f >nul 2>nul
reg delete "HKCU\Environment" /v OLLAMA_FLASH_ATTENTION /f >nul 2>nul
reg delete "HKCU\Environment" /v OLLAMA_KV_CACHE_TYPE /f >nul 2>nul
echo Done. Quit and restart Ollama for this to take effect.
pause
