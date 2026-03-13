import tkinter as tk
import ctypes
import time

# Find the window and move it
user32 = ctypes.windll.user32
hwnd = user32.FindWindowW(None, "AgentCreated2")

if hwnd:
    # Use SetWindowPos to move the window to (0,0)
    SWP_NOSIZE = 0x0001
    user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOSIZE)
    print("Window positioned at (0,0)")
else:
    print("Window not found")
