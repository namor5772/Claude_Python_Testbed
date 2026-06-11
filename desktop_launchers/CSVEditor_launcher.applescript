-- CSVEditor.app — Desktop launcher for CSVEditor.py
-- Per-machine artifact (absolute repo path); regenerate with osacompile if
-- the repo moves. Launches the editor detached; if one is already running,
-- brings its window to the front instead of starting a second instance
-- (needs a one-time Automation consent for System Events).

on run
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
			do shell script "cd " & quoted form of repoDir & " && nohup .venv/bin/python CSVEditor.py > /dev/null 2>&1 &"
		end if
	on error errMsg number errNum
		display dialog "CSVEditor launch failed (" & errNum & "): " & errMsg buttons {"OK"} default button 1 with icon stop with title "CSVEditor"
	end try
end run
