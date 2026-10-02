-- MyBackup.app — Desktop launcher for MyBackup.py
-- Per-machine artifact (absolute repo path); rebuild.sh patches the path for
-- whatever clone it runs from. Launches the app detached; if one is already
-- running, brings its window to the front instead of starting a second
-- instance (needs a one-time Automation consent for System Events). The
-- launch-or-focus matters here: two instances could mirror into the same TO
-- directory at the same time.
--
-- IMPORTANT: the subshell parentheses in `&& (nohup ... &)` are load-bearing — do
-- NOT "simplify" to `cd X && nohup ... &`. Under `do shell script`, a trailing &
-- on a COMPOUND list does not detach: the spawned sh waits on the child until the
-- app exits, so the applet never quits — and a still-running applet swallows the
-- next double-click (macOS sends reopen instead of relaunching), which breaks
-- the focus-the-running-app behaviour this launcher exists for. A & on a SIMPLE
-- command inside a foreground subshell detaches for real (verified + fixed
-- 2026-07-13 for CSVEditor.app and SelfBot.app — see SelfBot_launcher.applescript
-- for the full analysis). The reopen handler covers a second press landing in
-- the ~1s window while the applet is still alive.

on launchOrFocus()
	set repoDir to "/Users/roman/projects/Claude_Python_Testbed"
	try
		set foundPid to do shell script "pgrep -f 'MyBackup.py' | head -n 1; true"
		if foundPid is not "" then
			try
				tell application "System Events" to set frontmost of (first process whose unix id is (foundPid as integer)) to true
			on error
				display notification "MyBackup is already running" with title "MyBackup"
			end try
		else
			do shell script "cd " & quoted form of repoDir & " && (nohup .venv/bin/python MyBackup.py > /dev/null 2>&1 &)"
		end if
	on error errMsg number errNum
		display dialog "MyBackup launch failed (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "MyBackup"
	end try
end launchOrFocus

on run
	my launchOrFocus()
end run

on reopen
	my launchOrFocus()
end reopen
