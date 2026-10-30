@echo off
rem Push code and tags to GitHub.
rem   push.cmd          -> git push origin main --tags
rem   push.cmd force    -> overwrite remote main (only if nothing valuable there)
rem   push.cmd pull     -> merge remote changes, then push
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where git >nul 2>&1
if errorlevel 1 (
    echo Git not found in PATH
    exit /b 1
)

if /i "%~1"=="force" goto :force
if /i "%~1"=="pull" goto :pull

:push
echo Local changes:
git status --short
echo.
echo Pushing main and tags...
git push origin main --tags
if errorlevel 1 goto :fail
echo.
echo Done. Tags on GitHub:
git ls-remote --tags origin
exit /b 0

:pull
echo Merging remote changes...
git pull origin main --allow-unrelated-histories --no-rebase
if errorlevel 1 exit /b 1
git push origin main --tags
if errorlevel 1 goto :fail
echo Done.
exit /b 0

:force
echo Forcing push of main and tags...
git push --force origin main --tags
if errorlevel 1 goto :fail
echo Done.
exit /b 0

:fail
echo.
echo Push failed.
echo   push.cmd pull   - merge remote commits, then push
echo   push.cmd force  - overwrite remote main (loses remote commits)
echo Other reasons: wrong repo URL, not signed in, no access to vitrman/voice2text.
exit /b 1
