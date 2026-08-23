@echo off
setlocal
cd /d "%~dp0"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installation failed. Make sure Python 3.11+ is installed and available as "python".
  pause
  exit /b 1
)
echo.
echo ALFADEL dependencies installed.
pause
