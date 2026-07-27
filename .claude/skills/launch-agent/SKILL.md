---
name: launch-agent
description: Kill running Python instances and launch MyAgent.py
disable-model-invocation: true
---

Kill any running MyAgent instances, then launch MyAgent.py in the background with the venv's Python. Platform-specific — pick the branch matching the current OS:

**macOS:**
```bash
pkill -f "python.*MyAgent.py" 2>/dev/null; sleep 1
source .venv/bin/activate && nohup python MyAgent.py > /dev/null 2>&1 &
```

**Windows (Git Bash):**
```bash
taskkill //F //IM pythonw.exe 2>/dev/null; taskkill //F //IM python.exe 2>/dev/null
cmd.exe //c start //b .venv/Scripts/pythonw.exe MyAgent.py
```

Then confirm it is running (macOS: `pgrep -fl "python.*MyAgent.py"`; Windows: `tasklist | grep -i python`). Note the Windows kill is a blunt all-Python kill (it also takes down SelfBot etc.); the macOS pkill targets only MyAgent.
