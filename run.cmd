@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set HF_HOME=%CD%\.cache\hf
set TORCH_HOME=%CD%\.cache\torch
set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" app.py
