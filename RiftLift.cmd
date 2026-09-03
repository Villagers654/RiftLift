@echo off
setlocal
set "RIFTLIFT_HOME=%~dp0portable"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run install-windows.ps1 first.
  exit /b 1
)
if "%~1"=="" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m riftlift.windows_app
) else (
  "%~dp0.venv\Scripts\python.exe" -m riftlift.cli %*
)
exit /b %errorlevel%
