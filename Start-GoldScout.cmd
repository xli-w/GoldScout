@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo GoldScout's local Python environment was not found.
  echo Follow the one-time setup steps in docs\dashboard.md, then run this launcher again.
  pause
  exit /b 1
)
echo Start GoldScout in your browser at http://127.0.0.1:8765/
".venv\Scripts\python.exe" -m dashboard
