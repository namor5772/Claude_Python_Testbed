-- SelfBot.app — Desktop launcher for SelfBot.py
-- Per-machine artifact (absolute repo path); rebuild.sh rewrites the path for
-- whatever clone it runs from, then recompiles with osacompile. Launches the
-- chatbot detached; if one is already running, brings its window to the front
-- instead of starting a second instance (needs a one-time Automation consent for
-- System Events). This launches ONE solo SelfBot — the two-instance self-chat
-- (duo mode) is LaunchSelfBot.bat's job on Windows / launch it twice by hand.

on run
	set repoDir to "/Users/roman/projects/Claude_Python_Testbed"
	try
		set foundPid to do shell script "pgrep -f 'SelfBot.py' | head -n 1; true"
		if foundPid is not "" then
			try
				tell application "System Events" to set frontmost of (first process whose unix id is (foundPid as integer)) to true
			on error
				display notification "SelfBot is already running" with title "SelfBot"
			end try
		else
			do shell script "cd " & quoted form of repoDir & " && nohup .venv/bin/python SelfBot.py > /dev/null 2>&1 &"
		end if
	on error errMsg number errNum
		display dialog "SelfBot launch failed (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "SelfBot"
	end try
end run
