@echo off
setlocal
cd /d "%~dp0"
set "QUIET=0"
set "STARTUP_MODE=0"
set "PROBE_TIMEOUT=2"
set "MAX_ATTEMPTS=45"
set "RETRY_SECONDS=2"
if /I "%~1"=="/quiet" set "QUIET=1"
if /I "%~2"=="/quiet" set "QUIET=1"
if /I "%~1"=="/startup" set "STARTUP_MODE=1"
if /I "%~2"=="/startup" set "STARTUP_MODE=1"
if "%STARTUP_MODE%"=="1" (
  set "PROBE_TIMEOUT=1"
  set "MAX_ATTEMPTS=10"
  set "RETRY_SECONDS=1"
)
set "RESULT=0"
set "CHAT_MODEL=qwen2.5:1.5b"
for /f "usebackq delims=" %%I in (`powershell.exe -NoProfile -Command "$s = ConvertFrom-Json ([IO.File]::ReadAllText('config/settings.json')); $s.model_chat" 2^>nul`) do set "CHAT_MODEL=%%I"

echo Preparing Ollama for Study Buddy...
for /f "delims=" %%I in ('where ollama 2^>nul') do if not defined OLLAMA_EXE set "OLLAMA_EXE=%%I"
if not defined OLLAMA_EXE (
  echo Ollama was not found. Install it first from https://ollama.com/download/windows
  set "RESULT=1"
  goto finish
)

rem The Ollama desktop app may not have started yet at Windows sign-in.
curl.exe --silent --show-error --fail --max-time %PROBE_TIMEOUT% http://127.0.0.1:11434/api/tags >nul 2>nul
if errorlevel 1 (
  echo Ollama is not responding. Starting its local server...
  powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Process -FilePath $env:OLLAMA_EXE -ArgumentList 'serve' -WindowStyle Hidden" >nul 2>nul
)

set /a ATTEMPTS=0
:wait_for_ollama
curl.exe --silent --show-error --fail --max-time %PROBE_TIMEOUT% http://127.0.0.1:11434/api/tags >nul 2>nul
if not errorlevel 1 goto ollama_ready
set /a ATTEMPTS+=1
if %ATTEMPTS% GEQ %MAX_ATTEMPTS% if "%STARTUP_MODE%"=="1" (
  echo Ollama is still starting. Study Buddy will open and check again later.
  set "RESULT=1"
  goto finish
)
if %ATTEMPTS% GEQ 45 (
  echo Ollama did not become ready within 90 seconds.
  echo Study Buddy will still open, but chat may not work until Ollama is running.
  set "RESULT=1"
  goto finish
)
timeout /t %RETRY_SECONDS% /nobreak >nul
goto wait_for_ollama

:ollama_ready
echo Ollama is ready. Checking the Study Buddy chat model...
ollama list | findstr /I /C:"%CHAT_MODEL%" >nul
if not errorlevel 1 goto model_ready

if "%STARTUP_MODE%"=="1" (
  echo The configured chat model is not installed yet. Study Buddy will open; use fix_chat_model.bat to install it.
  set "RESULT=1"
  goto finish
)

echo The configured chat model %CHAT_MODEL% is missing. Downloading it now...
ollama pull %CHAT_MODEL%
if errorlevel 1 (
  echo Model download failed. You can retry with: fix_chat_model.bat
  set "RESULT=1"
  goto finish
)

:model_ready
echo Loading %CHAT_MODEL% into memory for a faster first reply...
powershell.exe -NoProfile -WindowStyle Hidden -Command "try { $s = ConvertFrom-Json ([IO.File]::ReadAllText('config/settings.json')); $o = $s.ollama_options; $body = @{model='%CHAT_MODEL%'; messages=@(@{role='user';content='Reply only with OK.'}); stream=$false; keep_alive=-1; options=@{num_gpu=0;num_thread=$o.num_thread;num_ctx=$o.num_ctx;num_predict=1;temperature=$o.temperature}} | ConvertTo-Json -Depth 5 -Compress; Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/chat' -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 90 | Out-Null } catch { exit 1 }" >nul 2>nul
if errorlevel 1 (
  echo The model is installed but did not preload. It will load on the first chat.
  set "RESULT=1"
) else echo The chat model is loaded and ready.

:finish
if "%QUIET%"=="0" pause
exit /b %RESULT%
