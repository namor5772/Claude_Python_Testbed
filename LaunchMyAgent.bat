@echo off
cd /d "%~dp0"

echo Launching MyAgent...
start "" .venv\Scripts\pythonw.exe MyAgent.py
exit
