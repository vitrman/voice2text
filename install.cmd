@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "TMP=%CD%\.cache\tmp"
set "TEMP=%TMP%"
set "UV_CACHE_DIR=%CD%\.cache\uv"
if not exist "%TMP%" mkdir "%TMP%"

set "PYBASE=C:\Users\vitrm\AppData\Local\Programs\Python\Python312\python.exe"
if not exist "%PYBASE%" (
    echo Python 3.12 not found: %PYBASE%
    echo Edit PYBASE in install.cmd
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    uv venv --python "%PYBASE%" .venv || goto :err
)

echo [1/3] torch CPU
uv pip install --python ".venv\Scripts\python.exe" torch torchaudio --index-url https://download.pytorch.org/whl/cpu || goto :err

echo [2/3] other dependencies
uv pip install --python ".venv\Scripts\python.exe" --no-deps numpy scipy sounddevice tqdm pyyaml omegaconf hydra-core pydub sentencepiece soundfile onnx onnxruntime packaging colorama pystray pillow six || goto :err

echo [3/3] gigaam v3 and antlr4
".venv\Scripts\python.exe" tools\bootstrap.py || goto :err

echo.
echo Done. Run: run.cmd
exit /b 0

:err
echo.
echo Install failed, code %ERRORLEVEL%
exit /b 1
