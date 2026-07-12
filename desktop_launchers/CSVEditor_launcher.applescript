-- CSVEditor.app — Desktop launcher for CSVEditor.py
-- Per-machine artifact (absolute repo path); regenerate with osacompile if
-- the repo moves. Launches the editor detached; if one is already running,
-- brings its window to the front instead of starting a second instance
-- (needs a one-time Automation consent for System Events).
--
-- IMPORTANT: the subshell parentheses in `&& (nohup ... &)` are load-bearing — do
-- NOT "simplify" to `cd X && nohup ... &`. Under `do shell script`, a trailing &
-- on a COMPOUND list does not detach: the spawned sh waits on the child until the
-- editor exits, so the applet never quits — and a still-running applet swallows
-- the next double-click (macOS sends reopen instead of relaunching), which broke
-- the focus-the-running-editor behaviour this launcher exists for. A & on a
-- SIMPLE command inside a foreground subshell detaches for real (verified + fixed
-- 2026-07-13, same fix as SelfBot.app — see SelfBot_launcher.applescript for the
-- full analysis). The reopen handler covers a second press landing in the ~1s
-- window while the applet is still alive.

on launchOrFocus()
	set repoDir to "/Users/roman/projects/Claude_Python_Testbed"
	try
		set foundPid to do shell script "pgrep -f 'CSVEditor.py' | head -n 1; true"
		if foundPid is not "" then
			try
				tell application "System Events" to set frontmost of (first process whose unix id is (foundPid as integer)) to true
			on error
				display notification "CSV Editor is already running" with title "CSVEditor"
			end try
		else
			do shell script "cd " & quoted form of repoDir & " && (nohup .venv/bin/python CSVEditor.py > /dev/null 2>&1 &)"
		end if
	on error errMsg number errNum
		display dialog "CSVEditor launch failed (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "CSVEditor"
	end try
end launchOrFocus

on run
	my launchOrFocus()
end run

on reopen
	my launchOrFocus()
end reopen
