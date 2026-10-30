@echo off
rem Publishes a GitHub release with the installer archive from dist-installer.
rem   release_github.cmd            -> gh CLI if signed in, else ask for a token
rem   release_github.cmd -DryRun    -> only show version, tag, archive path
rem   release_github.cmd -Repo owner/name
chcp 65001 >nul
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\publish_release.ps1" %*
if errorlevel 1 (
    echo.
    echo Release was not published. See messages above.
    pause
    exit /b 1
)
echo.
echo Done.
pause
