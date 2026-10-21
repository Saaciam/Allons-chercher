@echo off
title Mon stage - Interface de candidatures
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo L'environnement Python doit etre installe. Consulte README.md.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0web_app.py" --open
if errorlevel 1 pause
