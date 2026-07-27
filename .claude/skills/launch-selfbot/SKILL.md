---
name: launch-selfbot
description: Kill running Python instances and launch SelfBot.py
disable-model-invocation: true
---

Kill any running SelfBot instances, then launch SelfBot.py in the background with the venv's Python. Platform-specific — pick the branch matching the current OS:

**macOS:**
```bash
pkill -f "python.*SelfBot.py" 2>/dev/null; sleep 1
source .venv/bin/activate && nohup python SelfBot.py > /dev/null 2>&1 &
```

**Windows (Git Bash):**
```bash
taskkill //F //IM pythonw.exe 2>/dev/null; taskkill //F //IM python.exe 2>/dev/null
cmd.exe //c start //b .venv/Scripts/pythonw.exe SelfBot.py
```

Then confirm it is running (macOS: `pgrep -fl "python.*SelfBot.py"`; Windows: `tasklist | grep -i python`). For the dual-instance self-chat setup, use `LaunchSelfBot.bat` on Windows instead — it starts and positions both instances. Note the Windows kill is a blunt all-Python kill (it also takes down MyAgent etc.); the macOS pkill targets only SelfBot.
