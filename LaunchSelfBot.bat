@echo off
cd /d "%~dp0"

echo Killing existing instances...
taskkill /F /IM pythonw.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
ping -n 2 127.0.0.1 >nul

echo Cleaning up stale files...
del /q selfbot.lock selfbot_auto_msg.json selfbot_inject.txt >nul 2>&1

echo Launching Instance 1...
start "" .venv\Scripts\pythonw.exe SelfBot.py
ping -n 5 127.0.0.1 >nul

echo Launching Instance 2...
start "" .venv\Scripts\pythonw.exe SelfBot.py
ping -n 5 127.0.0.1 >nul

echo Positioning windows...
powershell -ExecutionPolicy Bypass -NoProfile -File "%~dp0selfbot_position.ps1"
exit
