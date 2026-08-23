@echo off
setlocal
cd /d "%~dp0"
python run_alfadel.py
if errorlevel 1 (
  echo.
  echo ALFADEL stopped with an error.
  pause
)
