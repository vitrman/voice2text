@echo off
rem Full build in one command: application + installer.
rem   release.cmd          -> dist\Voice2Text and dist-installer\*-installer.zip
rem   release.cmd trust    -> same + add certificate to trusted (no SmartScreen here)
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Run install.cmd first
    exit /b 1
)

echo [1/2] Application
call build.cmd %1
if errorlevel 1 goto :err

echo.
echo [2/2] Installer
".venv\Scripts\python.exe" installer\build_installer.py
if errorlevel 1 goto :err

echo.
echo Done. Installer archives:
dir /b dist-installer\*-installer.zip
echo Upload it to Releases: https://github.com/vitrman/voice2text/releases/new
exit /b 0

:err
echo.
echo Build failed
exit /b 1
