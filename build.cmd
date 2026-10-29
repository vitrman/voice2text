@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run install.cmd first
    exit /b 1
)
rem PyInstaller cannot overwrite files while the app is running
taskkill /IM Voice2Text.exe /F >nul 2>&1
ping -n 2 127.0.0.1 >nul

echo [1/3] Building exe
".venv\Scripts\pyinstaller.exe" voice2text.spec --noconfirm --clean
if errorlevel 1 (
    echo Build failed
    exit /b 1
)

echo [2/3] Copying model
if not exist "dist\models" mkdir "dist\models"
if not exist "dist\models\v3_e2e_rnnt.ckpt" (
    if exist ".cache\gigaam\v3_e2e_rnnt.ckpt" copy /Y ".cache\gigaam\v3_e2e_rnnt.ckpt" "dist\models\" >nul
)
if not exist "dist\models\v3_e2e_rnnt.ckpt" (
    if exist "%LOCALAPPDATA%\voice2text\.cache\gigaam\v3_e2e_rnnt.ckpt" copy /Y "%LOCALAPPDATA%\voice2text\.cache\gigaam\v3_e2e_rnnt.ckpt" "dist\models\" >nul
)
if not exist "dist\models\v3_e2e_rnnt_tokenizer.model" (
    if exist ".cache\gigaam\v3_e2e_rnnt_tokenizer.model" copy /Y ".cache\gigaam\v3_e2e_rnnt_tokenizer.model" "dist\models\" >nul
)

echo [3/3] Signing
set "SIGN_ARGS=-Exe dist\Voice2Text\Voice2Text.exe"
if /i "%~1"=="trust" set "SIGN_ARGS=%SIGN_ARGS% -Trust"
powershell -NoProfile -ExecutionPolicy Bypass -File tools\sign.ps1 %SIGN_ARGS%
if errorlevel 1 echo WARNING: signing failed, SmartScreen may warn

echo.
echo Done: dist\Voice2Text\Voice2Text.exe
echo Model: dist\models (kept between rebuilds, no re-download)
echo To silence SmartScreen on this PC: trust.cmd
