@echo off
cd /d "%~dp0"
echo Installing offline webcam and expression-recognition dependencies...
py -3.12 -m pip install opencv-python pillow onnxruntime numpy
if errorlevel 1 goto fail
if not exist models mkdir models
if not exist models\haarcascade_frontalface_default.xml (
  echo Downloading face detector XML...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/opencv/opencv/4.x/data/haarcascades/haarcascade_frontalface_default.xml' -OutFile 'models\haarcascade_frontalface_default.xml' -ErrorAction Stop"
  if errorlevel 1 goto fail
)
if exist models\emotion-ferplus-8.onnx (
  echo FER+ ONNX model already exists; keeping the existing local copy.
  goto done
)
echo Downloading the FER+ CNN ONNX model for local inference...
echo This is a one-time download. After it is saved, expression inference works offline.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; try { Invoke-WebRequest -Uri 'https://huggingface.co/onnxmodelzoo/emotion-ferplus-8/resolve/main/emotion-ferplus-8.onnx?download=true' -OutFile 'models\emotion-ferplus-8.onnx' -ErrorAction Stop } catch { Remove-Item 'models\emotion-ferplus-8.onnx' -ErrorAction SilentlyContinue; Invoke-WebRequest -Uri 'https://github.com/onnx/models/raw/main/vision/body_analysis/emotion_ferplus/model/emotion-ferplus-8.onnx' -OutFile 'models\emotion-ferplus-8.onnx' -ErrorAction Stop }"
if errorlevel 1 goto fail
if not exist models\emotion-ferplus-8.onnx goto fail
for %%A in (models\emotion-ferplus-8.onnx) do if %%~zA LSS 1000000 goto fail
:done
echo.
echo Setup complete. The FER+ model runs locally through ONNX Runtime on CPU.
echo No camera frame is saved or sent to Ollama/Llama; only a short text summary is shared with Buddy.
echo Camera access remains subject to Study Buddy Settings and explicit chat consent.
pause
exit /b 0
:fail
echo.
echo Camera setup failed. Check Python 3.12 and internet access, then retry this script.
echo Confirm models\emotion-ferplus-8.onnx downloaded successfully before running offline.
pause
exit /b 1
