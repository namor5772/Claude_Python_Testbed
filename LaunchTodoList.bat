@echo off
cd /d "%~dp0"

echo Launching TodoList...
start "" .venv\Scripts\pythonw.exe TodoList.py
exit
