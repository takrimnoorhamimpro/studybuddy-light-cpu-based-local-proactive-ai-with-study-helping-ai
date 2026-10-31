@echo off
cd /d "%~dp0"
echo Installing core Python dependencies...
py -3.12 -m pip install --upgrade pip
py -3.12 -m pip install requests cryptography psutil winotify
if errorlevel 1 goto fail
echo.
echo Core setup finished. Run setup_cpu_optimized.bat to download the recommended Qwen 2.5 1.5B CPU model.
pause
exit /b 0
:fail
echo Install failed. Check Python 3.12 and internet connection.
pause
exit /b 1
